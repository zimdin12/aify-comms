"""The live control plane: the helpers, constants and queues the route domains share.

~140 helpers, two queue classes and the constants behind status, dispatch, terminals, spawn and
console. It declares NO routes and owns no router — `service/routers/api_v2.py` is the composition
surface, and it is 53 lines of `include_router` with no re-export of anything here, so a stale
`from service.routers.api_v2 import <helper>` fails loudly instead of quietly resolving.

This file was `service/routers/api_v2.py`, 20,545 lines at its peak, until v0.5 moved the
reconcilers out and v0.5.2 moved the route domains out. By the end of that it declared zero routes:
a helper library living at a router's address. v0.5.3 moved it here and left the composition behind.

Its header until then still read "aify-comms v2 API — drop-in replacement for api.py", describing a
migration finished long before any of this. That was worth fixing rather than carrying: a file this
central whose first three lines are wrong teaches every reader something false before they reach the
code.

IT IS STILL FAR TOO BIG. Splitting 140 helpers by responsibility is a v0.6 question and deliberately
not a rename's job. Until then: put NEW behaviour in a leaf (`service/api_core/`,
`service/reconcilers/`, `service/status_engine.py`) and import it — do not grow this file.
"""
import asyncio
import json
import math
import sys
import logging
import sqlite3
from collections import deque
import itertools
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, NamedTuple, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError

# Per-agent wake-up events for comms_listen
_listen_events: dict[str, asyncio.Event] = {}

from pydantic import BaseModel
from service.pi_resident_flip import _drain_and_flip_pi_resident_agents
from service.api_core.status_decision import _decide_effective_status
from service.api_core.message_store import _delete_messages_by_ids, _get_unread_count_map
from service.config import get_config
# v0.5.1g: single owner, moved verbatim, same call timing.
# The CACHE is reached through the MODULE, never imported by value: a by-value import binds the dict
# this module saw at import time, and a second module-level assignment anywhere would then give
# writers and readers different dicts with nothing failing. Same rule, same reason, as
# `status_cache._LIVE_STATE_CACHE`. `test_process_global_identity` enforces it and caught this exact
# import when the move was first made.
from service.api_core import settings as settings_core
# v0.5.1i: single owner. The COUNTER is reached through the module, never by value.
from service.api_core import events as events_core
from service.api_core.dispatch_run_state import _append_dispatch_control, _finalize_dispatch_runs
from service.api_core.dispatch_text import _auto_handoff_body_for_run
from service.api_core.channel_delivery import _has_live_worker_for
from service.api_core.liveness import _LIVE_SESSION_STATUSES, _agent_liveness, _agent_wake_mode
from service.api_core.active_run_lookup import (
    _current_active_run_row,
    _current_channel_awaiting_reply_run_row,
    _find_mergeable_queued_run,
    _get_blocking_active_run,
)
from service.api_core.managed_env import _managed_environment_unavailable_reason
from service.api_core.events import (
    _append_dispatch_event,
    _append_terminal_control,
    _append_terminal_event,
    _TERMINAL_EVENT_CAP,
    _TERMINAL_EVENT_PRUNE_EVERY,
)
# v0.5.2a: the shared route class lives with the domain-router factory so no domain can build a
# router without the SQLite lock-retry. See service/api_core/routing.py.
from service.api_core.ws import _get_ws  # v0.5.1h: accessor only; manager stays on app.state
from service.api_core.settings import DEFAULT_SETTINGS, _invalidate_settings_cache, _load_settings
from service.api_core.validation import SAFE_NAME_RE, validate_name  # v0.5.1f: one owner
from service.api_core.runtime import (  # v0.5.1e: single owner, resolved against the contract
    _normalize_runtime,
    _normalize_session_mode,
    _runtime_capability_for_environment,
)
from service.api_core.serialization import (  # v0.5.1c: single owner, no copy
    _json_loads_or,
    _clip_text,
    _iso_from_ms,
    _dedupe_preserve,
    _timestamp_sort_key,
    _normalize_machine_id,
    _machine_ids_same_host,
    _quote_untrusted_subject,
    _row_require_reply,
)
from service.db import get_db, SQLITE_CLAIM_BUSY_TIMEOUT_MS
from service import longpoll
from service.usage_openai import collect_openai_pool
from service.terminal_diagnostics import (
    failure_tail as _terminal_failure_tail,
    meaningful_failure_line as _terminal_failure_line,
)
from service.terminal_snapshot import (
    render_snapshot as _render_terminal_snapshot,
    infer_source_width as _infer_terminal_source_width,
    feed_live_screen as _feed_live_terminal_screen,
    render_live_screen as _render_live_terminal_screen,
    resize_live_screen as _resize_live_terminal_screen,
    TERMINAL_MAX_COLS,
    TERMINAL_MAX_ROWS,
    drop_live_screen as _drop_live_terminal_screen,
)
from service.status_engine import apply_event, derive, StatusInputs, VALID_STATUSES
from service.dashboard_redirect import dashboard_url
from service.ntfy import notify_operator
from service.clock import now as _now
# v0.5 slice 1a. The status cache and the bridge reconcilers now live in their own module.
#
# FUNCTIONS are imported by name — safe, because a function object is never rebound. The CACHE DICT
# is deliberately NOT: `from ... import _LIVE_STATE_CACHE` would bind this module to whatever object
# existed at import time, and a later rebind in the owner would leave two dicts with reads and
# writes landing in different ones — silently. Reach it as `status_cache._LIVE_STATE_CACHE`.
# `service/tests/test_process_global_identity.py` fails the suite if that rule is broken.
from service.reconcilers import status_cache
from service.clock import iso_to_epoch as _iso_to_epoch
from service.env_status import environment_effective_status as _environment_effective_status
from service.api_core.dispatch_state import _get_dispatch_state_for_agent, _get_dispatch_state_map
from service.api_core.turn_state import (  # v0.5.4: moved out; the control plane is now a CALLER
    _clear_status_state_in_turn,
    _clear_turn_busy_if_no_open_reply_owing_run,
    _turn_busy_state,
)
from service.api_core.agent_sessions import (  # v0.5.4: moved out; the control plane is now a CALLER
    _agent_tombstone,
    _current_agent_session_row,
    _session_handle_live_owner,
    _tombstone_agent,
    _touch_agent,
    _touch_current_agent_session,
)
from service.api_core.channel_delivery import (  # v0.5.4: moved out; the control plane is now a CALLER
    _CHANNEL_CLAIM_RUNTIMES,
    _CHANNEL_FLAG_GATED_RUNTIMES,
    _CHANNEL_MANAGED_RUNTIMES,
    _CHANNEL_SIDECAR_DELIVERY_RUNTIMES,
    _WorkerLiveness,
    _apply_channel_routing_to_claude_runs,
    _channel_flag_enabled,
    _channel_managed_eligible,
    _insert_messages_via_console,
    _worker_liveness_for,
)
from service.api_core.virtual_rpc import (  # v0.5.4: moved out; the control plane is now a CALLER
    VIRTUAL_CODEX_RPC_COMMAND,
    VIRTUAL_HERMES_RPC_COMMAND,
    VIRTUAL_OPENCODE_RPC_COMMAND,
    VIRTUAL_PI_RPC_COMMAND,
    VIRTUAL_RPC_COMMANDS_BY_RUNTIME,
    VIRTUAL_RPC_COMMAND_SET,
)
from service.api_core.recovery_writes import (  # v0.5.4: moved out; the control plane is now a CALLER
    UNDELIVERED_CLAIM_REQUEUE_LIMIT,
    _record_channel_sidecar_heartbeat,
    _requeue_instead_of_failing_undelivered_claim,
)
from service.api_core.terminal_text import (  # v0.5.4: moved out; the control plane is now a CALLER
    _ANSI_RE,
    _CLAUDE_WORKING_FOOTER_RE,
    _terminal_awaiting_input_hint,
    _terminal_text_compact,
)
from service.api_core.managed_env import (  # v0.5.4: moved out; the control plane is now a CALLER
    _has_pending_or_booting_spawn_request,
    _managed_console_is_booting,
    _managed_environment_status,
    _managed_owning_environment_row,
    _managed_spawn_is_starting,
    _select_online_environment_for_runtime,
)
from service.api_core.liveness import (  # v0.5.4: moved out; the control plane is now a CALLER
    _has_live_claimer_lease,
    _has_recorded_claimer_lease,
    _resident_bridge_is_fresh,
    ACTIVE_RUN_BRIDGE_STALE_SECONDS,
    CHANNEL_SIDECAR_STALE_SECONDS,
    CONSOLE_WORKING_LEASE_SECONDS,
    _agent_has_live_terminal,
    _bridge_is_superseded,
    _claimer_lease_row,
    _console_working_lease_fresh,
    _has_live_channel_sidecar,
    _has_live_managed_wrapper_child,
    _has_live_terminal_session,
)
from service.api_core.reply_contract import (  # v0.5.4: moved out; the control plane is now a CALLER
    _contract_list_query,
    _contract_reminder_body,
    _contract_reminder_full_every,
    _is_operator_closed_contract,
    _message_satisfies_reply_contract,
)
from service.api_core.dispatch_text import (  # v0.5.4: moved out; the control plane is now a CALLER
    COLDSTART_REFUSED_PREFIX,
    _auto_handoff_subject_for_run,
    _build_pending_dispatch_subject,
    _coldstart_refusal_message,
    _format_dispatch_state,
    _is_provider_rate_limit_error,
    _render_pending_dispatch_item,
)
from service.api_core.records import (  # v0.5.4: moved out; the control plane is now a CALLER
    _agent_session_to_dict,
    _environment_record_to_dict,
    _terminal_session_to_dict,
)
from service.api_core.capabilities import (  # v0.5.4: moved out; the control plane is now a CALLER
    _default_capabilities_for,
    _default_console_command,
    _environment_supports_terminal,
    _environment_uses_windows_paths,
    _has_hermes_gateway_url,
    _has_live_rpc_controller,
    _managed_env_reachable,
    _managed_via_wrapper_for_runtime,
)
from service.env_status import _ENVIRONMENT_HEARTBEAT_STATUSES
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
# v0.5 slice 2: the spawn-lifecycle reconcilers moved to their own module.
from service.reconcilers.managed_workers import (
    _reconcile_managed_worker_hygiene,
    _repair_unusable_active_runs,
)
from service.reconcilers.dispatch_lifecycle import (
    _clear_turn_busy_for_dead_bridges,
    _close_orphaned_managed_runs,
    _close_steered_contracts_for_parent_run,
    _fail_stranded_delivered_reply_runs,
    _prune_orphaned_dispatch_runs,
    _sweep_unmirrored_failed_handoffs,
)
from service.reconcilers.dispatch_queue import (
    _close_reconcilable_delivered_runs,
    _reap_undeliverable_queued_runs,
    _replay_undelivered_channel_messages_on_env_recovery,
    _requeue_orphaned_claimed_runs,
    _reroute_orphaned_managed_channel_runs,
)
from service.reconcilers.terminal_runs import (
    _close_active_terminal_runs_for_terminal,
    _close_idle_claude_terminal_run_without_reply,
    _close_idle_pi_terminal_run_without_reply,
    _fail_pending_terminal_controls,
    _reconcile_ended_terminal_controls,
    _reconcile_stuck_terminal_and_session_rows,
)
from service.reconcilers.terminals import (
    _close_idle_virtual_rpc_workers,
    _prune_terminal_history,
    _reconcile_resurrected_managed_consoles,
    _reconcile_stale_managed_terminals_for_resident_agents,
)
from service.reconcilers.terminal_consistency import _repair_terminal_session_consistency
from service.reconcilers.sessions import (
    LIVE_SESSION_STATUSES,
    _compute_session_display_status,
    _reconcile_dead_session_status,
    _reconcile_duplicate_resident_sessions,
)
from service.reconcilers.spawn_lifecycle import (
    SPAWN_DEAD_TERMINAL_GRACE_SECONDS,
    _fail_orphaned_running_spawn_requests,
    _fail_running_spawns_superseded_by_current_session,
    _finalize_spawns_with_dead_terminals,
    _repair_spawn_requests_from_initial_dispatch_failures,
)
from service.reconcilers.status_cache import (
    BRIDGE_ORPHAN_STALE_SECONDS,
    _live_state_drop,
    _live_state_expire,
    _live_state_fresh,
    _live_state_get,
    _live_state_set,
    _prune_superseded_bridges,
    _reap_stale_orphan_bridges,
    stale_seconds_from_settings,
)
from service.usage_cache import (
    usage_set,
    usage_all,
    usage_get,
    derive_usage_source,
    consumption_set,
    consumption_summary,
)
from service.models import (
    AgentRegister, AgentStatusUpdate, AgentDescribeRequest, MessageSend, ClearRequest,
    ChannelCreate, ChannelMessage, ChannelJoin,
    AgentRuntimeStateUpdate, AgentSessionHandleUpdate, AgentSessionResolveRequest, AgentReadyUpdate, AgentSessionModeSwitchRequest, AgentResidentLostRequest, ConversationClearRequest, DispatchRequest, DispatchClaimRequest, DispatchRunUpdate,
    DispatchControlRequest, DispatchControlClaimRequest, DispatchControlUpdate,
    EnvironmentHeartbeat, EnvironmentControlRequest, EnvironmentControlClaim, EnvironmentControlUpdate, EnvironmentRootsUpdate,
    validate_model_shape,
    AgentEnvironmentAssignRequest, AgentRenameRequest, SpawnRequestCreate, SpawnRequestClaim, SpawnRequestUpdate, SessionControlRequest, AgentControlRequest,
    ConsoleStartRequest, TerminalControlRequest, TerminalControlClaim, TerminalControlUpdate, TerminalDeadReport, TerminalOutputRequest,
    VirtualTerminalEnsureRequest, AgentFavoriteUpdate, AgentConsoleInputRequest,
)

# _WINDOWS_DRIVE_CWD_RE moved to service/api_core/registration_gates.py in v0.5.4 —
# zero carrier readers, every consumer was a borrow accessor.
# _WSL_DRIVE_CWD_RE moved to service/api_core/registration_gates.py in v0.5.4 —
# zero carrier readers, every consumer was a borrow accessor.

logger = logging.getLogger("aify_comms.api_v2")

# The VIRTUAL_*_RPC_COMMAND sentinels and their map/set moved to
# service/api_core/virtual_rpc.py in v0.5.4 — a neutral leaf, because five
# unrelated subsystems compare against them.



# v0.5.3: the ROUTER COMPOSITION that used to live here moved to service/routers/api_v2.py,
# which is now nothing but composition. This module is the control plane: helpers, constants
# and the two queue classes. It declares no routes and owns no router.
# One router-owned console path still appends terminal output; it follows the helper to its new
# owner rather than keeping a second copy here.
from service.api_core.terminal_output import _append_terminal_output
from service.api_core.terminal_ownership import (  # v0.5.4: moved out; the carrier is a CALLER
    _active_terminal_for_agent,
    _release_stale_terminal_owner,
)
from service.api_core.dispatch_state import (  # v0.5.4: moved out; the carrier is a CALLER
    _DISPATCH_TERMINAL_STATUSES,
    _is_delivery_only_claude_run,
)
from service.api_core.dispatch_text import (  # v0.5.4: moved out; the carrier is a CALLER
    _MERGED_DISPATCH_HEADER,
    _pending_dispatch_count,
)
from service.api_core.reply_contract import _dispatch_reply_state  # v0.5.4: moved out
from service.api_core.execution_mode import (  # v0.5.4: both moved out of this file
    _agent_execution_mode,
    _auto_return_resident_to_managed_if_possible,
)
from service.api_core.active_run_discard import (  # v0.5.4: moved out; the carrier is a CALLER
    _discard_superseded_active_run,
    _discard_unclaimable_active_run,
    _discard_unusable_active_run,
    _fail_pending_controls_for_run,
    _fail_stale_active_run,
)
from service.api_core.dispatch_start import (  # v0.5.4: moved out; the carrier is a CALLER
    _coldstart_refusal,
    _coldstart_spawn_request_for_dispatch,
    _ensure_managed_pty_for_dispatch,
)
from service.api_core.workspace import (  # v0.5.4: moved out; the carrier is a CALLER
    _normalize_workspace_for_environment,
    _workspace_for_environment,
    _workspace_root_for,
)
from service.terminal_write_queue import (  # v0.5.4: moved out; the control plane is now a CALLER
    TERMINAL_OUTPUT_WRITES,
    TerminalOutputWriteQueue,
)  # noqa: E402
# v0.5.4: was imported from service.routers.terminals. The carrier reaching a LEAF through a
# ROUTER is the dependency direction this slice exists to reverse — leaving it would have kept
# the queue blocked while looking fixed.



_MANUAL_STATUSES = {"stopped"}

# Both terminal status sets now live in service/api_core/terminal_status.py, and the history is
# kept because the reasoning was overtaken twice. v0.5.3 moved _TERMINAL_MONOTONIC_STATUSES to
# service/routers/terminals.py with its only reader and recorded that _TERMINAL_ACTIVE_STATUSES
# had to STAY because the carrier read it; v0.5.4 moved both to a neutral leaf, because "the
# carrier reads it" was never a reason to own a constant — it is a reason to import one.
_RUNTIME_CONFIG_LIVE_KEYS = {
    "appServerUrl",
    "remoteAuthTokenEnv",
    "gatewayUrl",
    "gatewayTokenEnv",
    "channelEnabled",
}

# v0.5.1d: the vocabulary contract is the single owner of these words. Declared once in
# service/contracts/vocabulary.json, loaded by service/api_core/vocabulary.py, and cross-checked
# against the bridge's copy by an agreement test in each suite. Do NOT re-declare them here.
from service.api_core.vocabulary import (
    # RUNTIME_ALIASES is deliberately absent: its only consumer, _normalize_runtime, moved to
    # service/api_core/runtime.py in v0.5.1e, and importing a name nobody reads is how a module
    # keeps looking like the owner of something it no longer touches.
    LAUNCHABLE_RUNTIMES as _LAUNCHABLE_RUNTIMES,
    SESSION_MODES as _SESSION_MODES,
)
# _DISPATCH_TERMINAL_STATUSES moved to service/api_core/dispatch_state.py in v0.5.4.
# _TERMINAL_END_STATUSES and _TERMINAL_END_STATUSES_ORDERED moved to
# service/api_core/terminal_status.py in v0.5.4, together — the ordered form is DERIVED from
# the set and a test guards their agreement, so the derivation must not span a module boundary.
_DISPATCH_ACTIVE_STATUSES = {"queued", "claimed", "running"}
_SESSION_DELETE_ALLOWED_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
# A session whose spawn/run is in flight or live. "starting" is included so a
# spawn-in-progress is not marked offline merely because the environment bridge
# instance id rotated (same rationale as a running session surviving a bridge
# restart); genuine staleness is still caught by env-offline/heartbeat checks.
# ENDED_AGENT_SESSION_STATUSES moved to service/api_core/agent_sessions.py in v0.5.4
# (sole-reader chain: the whole derivation had one consumer).
# _ENDED_AGENT_SESSION_STATUS_PARAMS moved to service/api_core/agent_sessions.py in v0.5.4
# (sole-reader chain: the whole derivation had one consumer).
# _ENDED_AGENT_SESSION_STATUS_PLACEHOLDERS moved to service/api_core/agent_sessions.py in v0.5.4
# (sole-reader chain: the whole derivation had one consumer).
# Terminal-session (terminal_sessions.status) end states: a managed session's
# backing console/worker is DEAD when its owning terminal row is in this set (or
# the terminal row is absent). Used by the new deriver + the dead-session
# reconcile case (a) to join the LIVE terminal truth instead of the frozen
# agent_sessions.terminal_status denorm.
TERMINAL_DEAD_STATUSES = {"failed", "stopped", "exited", "lost", "ended", "cancelled"}
# Terminal reached an end state (distinct from the transient "stopping").
_TERMINAL_DEAD_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
# A bridge-pushed turn_busy=1 is "working" only if refreshed within this
# window; the bridge re-sends true on every per-agent heartbeat during long
# turns (keep its cadence well under this).
#
# WS5 Task 5.2/5.3 (2026-06-02) — DEMOTED to a BACKSTOP. The PRIMARY off-`working`
# transition is now a real turn-END EVENT (POST /turn-end): claude's Stop hook,
# codex turn/completed + pi agent_end (native run terminal), and — newly — managed
# hermes observing its gateway session go idle (hermes-managed-host.js
# makeInFlightProbe → clearTurn). The event clears turn_busy=0 the instant a turn
# ends, so this window now ONLY fires when an end event is DROPPED. NOTE (2026-07-26):
# this is NO LONGER the claim-gate window. The delivery gates read the raw flag bounded
# by TURN_BUSY_BACKSTOP_SECONDS (_turn_busy_holds_delivery); this 120s value now only
# serves _turn_busy_state (reminder-loop busy definition). The
# STATUS staleness
# window is the longer TURN_BUSY_BACKSTOP_SECONDS so a missed end event self-heals
# at the single long wall-clock ceiling instead of flapping against the re-pulse
# cadence (the prior 120s-vs-45s race produced the false-working flap). Never key a
# re-arm of turn_busy on derived status — only the bridge sets it and only an event
# (or this backstop) clears it (anti-feedback-loop invariant).
# TURN_BUSY_STALE_SECONDS moved to service/api_core/turn_state.py in v0.5.4 with its only
# reader. NOT the same bound as TURN_BUSY_BACKSTOP_SECONDS, which stays here.
# Console-working lease (2026-06-05): the managed-claude PTY spinner footer refreshes
# this lease every TERMINAL re-emit. When the dashboard Console is CLOSED, claude would
# otherwise go quiet on the PTY (it only re-emits its footer while actively rendered), so
# the bridge runs a ~4s repaint keepalive (terminal-runtime._armConsoleKeepalive) that
# SIGWINCHes the PTY to force a footer re-emit. The TTL spans that keepalive cadence — a
# small multiple of ~4s so a missed poke or two never drops `working`, yet it still
# self-expires within seconds of claude truly stopping. ADDITIVE only: OR'd into derived
# `working`, it never clears turn_busy.
# CONSOLE_WORKING_LEASE_SECONDS moved to service/api_core/liveness.py in v0.5.4, with the
# predicates that apply it — a threshold apart from its predicate is how they drift.
# pure-event-status change #3 (2026-06-02): STATUS is now PURE-EVENT. The
# turn-START event sets turn_busy=1 → working; the turn-END event clears
# turn_busy=0 → idle, INSTANTLY (the /turn-end POST invalidates the live-status
# cache, so the transition does not wait on any timer). The seconds window is NO
# LONGER the deciding factor for STATUS.
#
# This LONG ceiling is the dropped-event SELF-HEAL ONLY — it catches a MISSED
# turn-end on a STILL-ALIVE agent (e.g. a claude Stop hook that didn't fire and
# whose transcript-detector backstop also somehow missed). It is NOT a primary
# transition: the event clears instantly, and the 60s reconcile + cache
# invalidation recompute, so a real dropped event self-heals at this single long
# wall-clock ceiling rather than flapping against any re-pulse cadence.
#
# The earlier 15-min-then-reverted-to-120s saga (commit 0fc84e6) collapsed this
# to 120s ONLY because the turn-END event was UNRELIABLE in live use, so the long
# window became the effective status window and idle agents showed `working`. That
# root cause is fixed by change #1 (the hook-independent transcript turn-END
# detector) + change #2 (liveness wins over turn_busy), so the long ceiling is
# now safe to restore: a missed Stop hook is caught by the detector in ~30s, and a
# dead worker is caught by the liveness lease — only a still-alive agent with BOTH
# event paths missed reaches this 30-min ceiling, which is exactly its purpose.
# ALSO the delivery gates' anti-strand bound (2026-07-26). The gates key on the RAW
# turn_busy flag so "explicit queue" means exactly "after this turn" (#236), and this
# ceiling is the single bound on that raw read — see _turn_busy_holds_delivery. Using
# the SAME ceiling as status is the invariant that matters: past it, derive() already
# reports the agent as not-in-turn, so delivery must not still be holding, or an
# abandoned flag strands queued work forever (and a target without `steer` goes
# permanently deaf).
#
# ANTI-FEEDBACK-LOOP: only a bridge/event sets turn_busy; only an event/this
# ceiling/the run-reply clear clears it. Status is NEVER read back to re-arm it.
# TURN_BUSY_BACKSTOP_SECONDS moved to service/api_core/liveness.py in v0.5.4 — it is a
# liveness threshold, and it must stay equal to the status engine's in_turn clamp.
# (TURN_END_GRACE_SECONDS removed 2026-06-19 — status is pure-event; the grace flap-absorber
# was deleted from both status paths and the flap is fixed at the bridge source.)
# Poll-load fix (2026-06-18): a settled `offline` agent's cached status only changes via an
# explicit cache-invalidating event (a returning heartbeat/turn/operator action all DELETE the
# row). Its refresh_after is otherwise `last_seen + liveness`, which is ANCIENT for a long-dead
# agent — so every roster poll re-derived + re-PERSISTED every offline agent, saturating SQLite's
# single writer (observed: 16/29 agents permanently expired -> sustained `database is locked`).
# Give offline a moderate future horizon so the hot read path serves cache; the reconcile sweep
# still re-validates each offline agent ~every interval (env-return safety), and recovery is
# immediate via invalidation. Tune via the agent_offline_revalidate_seconds setting.
OFFLINE_CACHE_REVALIDATE_SECONDS = 180
# Runtimes with native managed adapters. Codex/Hermes may be promoted to the
# wrapper-backed channel path by managed_via_wrapper; otherwise these runtimes
# are claimed by the bridge's native controller. PTY-input is a legacy
# explicit opt-in only (insert_messages_via_console=true).
# Managed Claude uses a live Claude Code channel bridge. It is not a native
# managed runtime adapter and must not be claimed by the generic managed loop.
# Membership controls two distinct behaviors:
#   1. _agent_execution_mode (line 1063) returns 'channel' unconditionally for
#      these runtimes' managed dispatches — claude has no headless managed-run,
#      so all managed claude flows through claude-channel.js.
#   2. The PTY-spawn carve-outs at 9891/9917 fire only for these runtimes.
# Codex/hermes/pi support a real native managed-run path and must NOT be
# auto-routed to channel by membership alone — they route to channel ONLY when
# wrapper-backed (managed_via_wrapper setting includes codex/hermes; gate at
# line 1047). Pi stays native RPC. See _CHANNEL_CLAIM_RUNTIMES below for the
# claim-side whitelist.
# _CHANNEL_MANAGED_RUNTIMES moved to service/api_core/channel_delivery.py in v0.5.4.
# Runtimes that route managed dispatches to execution_mode='channel' ONLY when
# the wrapper has set the channel-enabled runtime flag (runtime_config
# .channelEnabled, exported by the *-aify wrapper as AIFY_CHANNELS_ENABLED=1 —
# the SAME mechanism claude uses; see autoRegisterConfiguredAgent in
# mcp/stdio/server.js). This is the symmetric-with-claude delivery path: an
# channel-sidecar (for hermes: the `hermes-managed-host.js run <agent>` gateway
# delivery loop, the analogue of claude-channel.js) claims the channel run and
# delivers the wake; the agent self-replies via comms_send.
# ASYMMETRY(hermes): claude is in _CHANNEL_MANAGED_RUNTIMES and routes to
# channel UNCONDITIONALLY (claude has no headless managed-run API). hermes DOES
# have a native managed path, so it routes to channel only when the wrapper
# flag is present; without the flag it stays on its prior native/managed route
# (no false channel-deliverability claim). Membership here intentionally does
# NOT pull hermes into the claude-specific PTY-backing carve-outs keyed on
# _CHANNEL_MANAGED_RUNTIMES (those assume claude-aify hosts the sidecar); the
# hermes gateway delivery loop is a standalone per-agent process (Task 1.1/1.2).
# _CHANNEL_FLAG_GATED_RUNTIMES moved to service/api_core/channel_delivery.py in v0.5.4.
# Claim-side whitelist for execution_mode='channel' runs. Claude channel
# claims can come from the claude-aify channel bridge. Wrapper-backed managed
# Codex/Hermes claims must come from the wrapper PTY child bridge registered
# as bridge_kind='managed-wrapper-child'; the main environment bridge is
# intentionally blocked from claiming them because it lacks the live local
# app-server/gateway for the visible console. opencode is intentionally
# excluded — its adapter declares preferred_delivery_mode='managed'.
# _CHANNEL_CLAIM_RUNTIMES moved to service/api_core/channel_delivery.py in v0.5.4.
# Managed runtimes whose dispatches are delivered ONLY by a SEPARATE
# channel-sidecar process (bridge_kind='channel-sidecar'), where the visible
# wrapper PTY merely RENDERS and never claims. For these, a live PTY does NOT
# prove deliverability, so `online` REQUIRES a live, non-superseded
# channel-sidecar — overriding the PTY-derived has_live_worker (status-F1,
# operator-reported 2026-05-31: managed claude showed online + "Console ready"
# while its superseded sidecar delivered nothing and runs sat queued).
#
# claude-code: claude-aify's claude-channel.js sidecar is the sole claimer
# (wrapperChildExecutionModes excludes claude, so the PTY never claims).
#
# hermes (added WS3 Task 3.1, 2026-06-02): the managed-hermes lifecycle re-
# architecture makes the delivery loop (hermes-managed-host.js) the SINGLE
# claimer + lifecycle owner, registered as a `channel-sidecar` bridge, fronting a
# visible console PTY. A live console PTY ALONE no longer proves deliverability
# (the loop/claimer can be dead while the gateway/console still renders — the
# operator-observed "online but deaf" bug). So managed hermes now goes through
# the same both-required gate (sidecar_live AND console_live) as claude: `online`
# REQUIRES a live channel-sidecar claimer. This SUPERSEDES the prior "two delivery
# models" rationale (the wrapper-child claim variant is retired in favor of the
# single loop owner); the legacy channelEnabled no-PTY gate below still covers a
# channel-flag hermes that registers a sidecar but never opens a console.
#
# codex/pi: their wrapper-child / RPC worker IS the claimer, so PTY liveness
# already equals deliverability.
# _CHANNEL_SIDECAR_DELIVERY_RUNTIMES moved to service/api_core/channel_delivery.py in v0.5.4.


# _managed_via_wrapper_for_runtime moved to service/api_core/capabilities.py in v0.5.4.


# _channel_flag_enabled moved to service/api_core/channel_delivery.py in v0.5.4.


# _channel_managed_eligible moved to service/api_core/channel_delivery.py in v0.5.4.


# _has_live_terminal_session moved to service/api_core/liveness.py in v0.5.4.


# _has_live_rpc_controller moved to service/api_core/capabilities.py in v0.5.4.


# A channel-sidecar bridge heartbeat older than this is treated as a dead
# sidecar for deliverability/status purposes. The standalone sidecar's
# /dispatch/claim poll loop refreshes bridge_instances.last_seen on every tick
# (claim_dispatch: "the claim poll itself is the heartbeat"), so a live sidecar
# stays well within this window; a process that has exited goes stale quickly.
# CHANNEL_SIDECAR_STALE_SECONDS moved to service/api_core/liveness.py in v0.5.4, with the
# predicates that apply it — a threshold apart from its predicate is how they drift.

# WS5 Task 5.1 (2026-06-02): an ACQUIRED claimer lease that has not been
# refreshed within this window is treated as stale (the loop died without
# POSTing `claimer-release` — e.g. SIGKILL / crash). The loop refreshes its
# lease on every successful /dispatch/claim round-trip (same cadence as the
# channel-sidecar heartbeat), so a live loop stays well inside this window.
# A clean `claimer-release` makes the lease not-live IMMEDIATELY (no wait);
# this window only backstops a MISSED release. Kept longer than the sidecar
# stale window so the lease is never the FIRST signal to expire on a live loop.
# CLAIMER_LEASE_STALE_SECONDS moved to service/api_core/liveness.py in v0.5.4 with its only reader.

# Workstream B2 (2026-06-01): grace before a managed claude with a LIVE sidecar
# but a DEAD console PTY is treated as a headless orphan worker. Must exceed the
# 30s liveness beat + console startup so a transiently-restarting console (PTY
# respawn between beats) is never falsely reaped.
MANAGED_ORPHAN_GRACE_SECONDS = 90


# _has_live_channel_sidecar moved to service/api_core/liveness.py in v0.5.4.


# _has_live_managed_wrapper_child moved to service/api_core/liveness.py in v0.5.4.


# _claimer_lease_row moved to service/api_core/liveness.py in v0.5.4.


# _has_live_claimer_lease moved to service/api_core/liveness.py in v0.5.4.


# _has_recorded_claimer_lease moved to service/api_core/liveness.py in v0.5.4.












# _insert_messages_via_console moved to service/api_core/channel_delivery.py in v0.5.4.


# _apply_channel_routing_to_claude_runs moved to service/api_core/channel_delivery.py in v0.5.4.




# ACTIVE_RUN_BRIDGE_STALE_SECONDS moved to service/api_core/liveness.py in v0.5.4, with the
# predicates that apply it — a threshold apart from its predicate is how they drift.
# CLAUDE_RESIDENT_DELIVERY_SUMMARY_PREFIX moved to service/api_core/dispatch_state.py in v0.5.4.
# CLAUDE_CHANNEL_DELIVERY_SUMMARY_PREFIX moved to service/api_core/dispatch_state.py in v0.5.4.




# _touch_agent moved to service/api_core/agent_sessions.py in v0.5.4.
















_SHELL_PLACEHOLDER_HANDLE_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")





# _machine_family moved to service/routers/agents/shared.py in v0.5.3 — the agents package was its
# only consumer once the domains moved, so the borrow shim became the last thing keeping it here.












# _is_delivery_only_claude_run moved to service/api_core/dispatch_state.py in v0.5.4.


# _dispatch_reply_state moved to service/api_core/reply_contract.py in v0.5.4.


# _dispatch_reply_pending moved to service/routers/dispatch_messages/shared.py in v0.5.3 — the
# dispatch+messages package was its only consumer. `_dispatch_reply_state`, which it calls, is still
# router-owned and stays borrowed there.


# _is_operator_closed_contract moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_reply_expected moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_state moved to service/api_core/reply_contract.py in v0.5.4.






# _has_codex_live_app_server moved to service/api_core/capabilities.py in v0.5.4.


# _has_hermes_gateway_url moved to service/api_core/capabilities.py in v0.5.4.








async def _select_message_ids(db, where_clause: str, params: tuple[Any, ...] = ()) -> list[str]:
    cursor = await db.execute(f"SELECT id FROM messages WHERE {where_clause}", params)
    return [str(row["id"]) for row in await cursor.fetchall() if str(row["id"] or "").strip()]



async def _delete_messages_where(db, where_clause: str, params: tuple[Any, ...] = ()) -> int:
    message_ids = await _select_message_ids(db, where_clause, params)
    return await _delete_messages_by_ids(db, message_ids)


# _agent_tombstone moved to service/api_core/agent_sessions.py in v0.5.4.


# _tombstone_agent moved to service/api_core/agent_sessions.py in v0.5.4.


# _remove_agent_record moved to service/api_core/agent_removal.py in v0.5.4.


# _default_capabilities_for moved to service/api_core/capabilities.py in v0.5.4.




# _row_capabilities moved to service/api_core/capabilities.py in v0.5.4.


def _row_status_note(row) -> str:
    if not row or "status_note" not in row.keys():
        return ""
    return str(row["status_note"] or "").strip()



# _agent_execution_mode moved to service/api_core/execution_mode.py in v0.5.4.



# _dispatch_fix_hint moved to service/api_core/dispatch_hint.py in v0.5.4.


# _format_dispatch_state moved to service/api_core/dispatch_text.py in v0.5.4.


# _get_dispatch_state_for_agent moved to service/api_core/dispatch_state.py in v0.5.4.


# _get_dispatch_state_map moved to service/api_core/dispatch_state.py in v0.5.4.



# _resident_bridge_is_fresh moved to service/api_core/liveness.py in v0.5.4.


# _agent_has_live_terminal moved to service/api_core/liveness.py in v0.5.4.


# TODO consolidate existing *_is_fresh helpers (_resident_bridge_is_fresh,
# _owner_bridge_is_fresh, _agent_has_fresh_bridge, _has_live_channel_sidecar,
# _has_live_managed_wrapper_child, _has_live_terminal_session) into
# _agent_liveness — deferred this pass (their many callers make ripping them out a
# separate, risky migration). For now _agent_liveness is the SINGLE predicate the
# new session deriver uses; the legacy helpers stay for their existing callers.

# _turn_busy_state moved to service/api_core/turn_state.py in v0.5.4.


# _turn_busy_holds_delivery moved to service/routers/dispatch_messages/shared.py in v0.5.3.




# _session_handle_live_owner moved to service/api_core/agent_sessions.py in v0.5.4.





# _bridge_is_superseded moved to service/api_core/liveness.py in v0.5.4.


# _active_wrapper_terminal_id moved to service/api_core/claim_gating.py in v0.5.4.


# _ANSI_RE was declared HERE as well until v0.5.3, with a NARROWER pattern that did not strip
# DCS/APC/PM/SOS strings. It was dead: the declaration further down rebinds the name before
# any of these functions can run, so every caller — including _terminal_text_compact just
# below — already used the broader one. Removed because it read like the governing
# definition for the function beneath it and was not.


# _terminal_text_compact moved to service/api_core/terminal_text.py in v0.5.4.


# _hermes_terminal_still_resuming moved to service/api_core/claim_gating.py in v0.5.4.


# _active_wrapper_terminal_not_ready_reason moved to service/api_core/claim_gating.py in v0.5.4.


# _bridge_claim_block_reason moved to service/routers/dispatch_messages/shared.py in v0.5.3.






STUCK_STOPPING_GRACE_SECONDS = 900  # a 'stopping' PTY that never reached 'stopped' is wedged








# _record_channel_sidecar_heartbeat moved to service/api_core/recovery_writes.py in v0.5.4.




# _stop_virtual_terminals_for_superseded_bridges moved to service/routers/agents/shared.py in v0.5.3.


# _fail_active_runs_for_superseded_bridges moved to service/routers/agents/shared.py in v0.5.3.


# _fail_pending_controls_for_run moved to service/api_core/active_run_discard.py in v0.5.4.


def _status_with_dispatch(status: str, dispatch_state: Optional[dict[str, Any]]) -> str:
    # Only an actively-RUNNING dispatch run means the agent is 'working'. A merely
    # 'claimed' run (a bridge claimed it to deliver, but the turn hasn't started — or
    # the agent already finished and the run just isn't closed yet) does NOT: an idle
    # agent with a stale claimed run must reflect the engine's turn_busy-based verdict
    # (online), not a phantom 'working'. This was the root cause of agents showing
    # 'working' while actually idle (2026-06-18). The running→working promotion is kept
    # so a just-delivered turn reads 'working' before the bridge's turn-start event lands.
    if not dispatch_state:
        return status
    active = dispatch_state.get("activeRun") or {}
    if active.get("status") == "running" and status not in _MANUAL_STATUSES and status not in {"stale", "offline", "blocked"}:
        return "working"
    return status


# Legacy raw agents.status values that predate the proof-based 6-status vocabulary. The
# bridge heartbeat still stamps agents.status='active', and older DBs may carry 'idle'/
# 'stale' rows; if a live_state row is ever missing, that raw value must NOT leak to the UI
# as a non-canonical status. 'stale' was a time-decay state removed 2026-06-18 — any lingering
# row normalizes to 'offline' (the proof-based engine never writes it; the cleanup writer that
# used to stamp it was removed in the same pass).
_LEGACY_RAW_STATUS_TO_CANONICAL = {"active": "online", "idle": "online", "stale": "offline"}


def _agent_record_to_dict(row, status: str, unread: int, dispatch_state: Optional[dict[str, Any]] = None, *, live_reason: Optional[str] = None, outbound: Optional[dict[str, Any]] = None):
    runtime = _normalize_runtime(row["runtime"] or "generic")
    session_mode = _normalize_session_mode(row["session_mode"] or "resident")
    # live_reason is the derived status reason from the in-memory cache (the live_state table
    # was retired 2026-06-18). Fall back to the row's live_reason column (legacy/JOIN paths) or
    # the raw status note. `status` carries the derived live status from the cache.
    status_note = str(
        (live_reason if live_reason is not None else (row["live_reason"] if "live_reason" in row.keys() else ""))
        or _row_status_note(row) or ""
    ).strip()
    base_status = str((row["live_status"] if "live_status" in row.keys() else "") or status or row["status"] or "idle").strip()
    # `ready` is an internal bridge/controller readiness bit. Keep it out of
    # the public agent taxonomy so operators see one idle-live state: online.
    if base_status.lower() == "ready":
        base_status = "online"
    # Never surface a legacy raw status (e.g. heartbeat-stamped 'active') as-is.
    base_status = _LEGACY_RAW_STATUS_TO_CANONICAL.get(base_status.lower(), base_status)
    effective_status = _status_with_dispatch(base_status, dispatch_state)
    # Usage/quota (2026-06-26): bind the agent to a quota pool (explicit override in
    # runtime_config.usageSource, else derived from runtime) and merge that pool's live
    # remaining %% from the in-memory usage cache. Advisory only — never gates anything.
    _rc_for_usage = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
    _usage_source = (_rc_for_usage.get("usageSource") if isinstance(_rc_for_usage, dict) else None) or derive_usage_source(runtime, _rc_for_usage)
    _pool = usage_get(_usage_source) if _usage_source else None
    return {
        "role": row["role"],
        "name": row["name"],
        "cwd": row["cwd"],
        "model": row["model"],
        "description": (row["description"] if "description" in row.keys() else "") or "",
        "instructions": row["instructions"],
        "status": effective_status,
        "statusRaw": effective_status,
        "statusNote": status_note,
        "registeredAt": row["registered_at"],
        "lastSeen": row["last_seen"],
        "unread": unread,
        "runtime": runtime,
        "usageSource": _usage_source or "",
        "poolWeeklyPctLeft": ((_pool.get("weekly") or {}).get("left_pct") if _pool else None),
        "poolSeverity": ((_pool.get("severity") if _pool else None) or ""),
        "quotaCritical": bool(_pool and _pool.get("severity") == "critical"),
        "machineId": row["machine_id"] or "",
        "launchMode": row["launch_mode"] or "detached",
        "sessionMode": session_mode,
        "wakeMode": _agent_wake_mode(row),
        "sessionHandle": row["session_handle"] or "",
        # Sticky session identity (governance, 2026-05-30): a non-empty
        # pendingSessionId means the agent reported an in-session id different
        # from its persisted handle; delivery still targets sessionHandle and
        # the dashboard shows a `session-changed` badge with Confirm/Keep
        # actions until the operator resolves it.
        "pendingSessionId": (row["pending_session_id"] if "pending_session_id" in row.keys() else "") or "",
        "sessionChanged": bool((row["pending_session_id"] if "pending_session_id" in row.keys() else "") or ""),
        "managedBy": row["managed_by"] or "",
        "capabilities": _row_capabilities(row),
        "runtimeConfig": _json_loads_or(row["runtime_config"], {}),
        "runtimeState": _json_loads_or(row["runtime_state"], {}),
        "dispatchState": dispatch_state or {"hasActiveRun": False, "activeRun": None, "queuedRuns": 0},
        # What this agent last PRODUCED. Every other field here answers about inbound traffic or
        # registration liveness; see _get_outbound_activity_map for the false "silent lane" claim
        # that absence caused. Empty dict when unknown — never a fabricated timestamp.
        "outbound": outbound or {},
        "favorited": bool(int((row["favorited"] if "favorited" in row.keys() else 0) or 0)),
        # Dashboard rendering hint: resident sessions live in an
        # operator-launched terminal outside aify's PTY tracking — the
        # dashboard's "Start Console" button can't open or attach to
        # them, so the dashboard should hide the button for these.
        # Managed sessions have either a real wrapper PTY OR a
        # synthesized virtual rpc terminal — Console attaches to either.
        "consoleAvailable": session_mode != "resident",
    }


#: Stored environment statuses that still claim a HEARTBEATING bridge, and must therefore be aged
#: against `last_seen`. `degraded` belongs here: a degraded bridge is reduced-capability, not
#: dead — so when it stops heartbeating it is just as offline as an `online` one.
# NOT declared here. v0.5 slice 2 moved this to service/env_status.py and left a COPY behind --
# equal values, two objects, and nothing would have failed if one had been edited. The reviewer
# ruled at the time that a moved constant gets exactly one owner and never a second copy; this is
# that ruling finally applied. It is imported beside its only user, _environment_effective_status.




# _managed_owning_environment_row moved to service/api_core/managed_env.py in v0.5.4.


# _managed_env_reachable moved to service/api_core/capabilities.py in v0.5.4.


# _environment_record_to_dict moved to service/api_core/records.py in v0.5.4.


def _iso_add_seconds(value: str, seconds: int) -> str:
    # Compose the canonical parse/format helpers so refresh_after timestamps use
    # the same second-precision "...Z" form as _now() (what they're compared to).
    epoch = _iso_to_epoch(value)
    if not epoch:
        return ""
    return _iso_from_ms(int((epoch + max(0, int(seconds))) * 1000))


def _status_refresh_after(agent_last_seen: str, env_last_seen: str, *, liveness_seconds: int, env_offline_seconds: int) -> str:
    # Cache TTL keyed on the liveness windows (proof-based, 2026-06-18): recompute when the
    # agent could cross its liveness window or its env could go offline — no idle/offline
    # minute decay. The reconcile sweep + push events also keep the cache fresh.
    candidates = [
        _iso_add_seconds(agent_last_seen, int(liveness_seconds or 0)),
        _iso_add_seconds(env_last_seen, int(env_offline_seconds or 0)),
    ]
    candidates = [value for value in candidates if value]
    return min(candidates) if candidates else ""


# _current_agent_session_row moved to service/api_core/agent_sessions.py in v0.5.4.



# _ANSI_RE moved to service/api_core/terminal_text.py in v0.5.4 with its readers.


# _CLAUDE_WORKING_FOOTER_RE moved to service/api_core/terminal_text.py in v0.5.4 with its readers.


# _terminal_awaiting_input_hint moved to service/api_core/terminal_text.py in v0.5.4.


# _console_working_lease_fresh moved to service/api_core/liveness.py in v0.5.4.


# ── Prompt detection reads the SCREEN, not the byte log (2026-07-14) ────────────────────
#
# Claude's TUI does not emit words separated by spaces — it paints each one with cursor-
# positioning escapes (`ESC[nG`). Strip the ANSI from the raw PTY log and the screen collapses
# into `Resumingthefullsessionwillconsumeasubstantialportionofyourusagelimits.` — so every
# multi-word regex below missed, and an agent STUCK on the compaction dialog produced no hint
# at all. It rendered as `working`: not merely "no blocked badge", but actively busy-looking
# while doing nothing. (Live: `lc-manager` sat at the dialog with awaiting_input=0.)
#
# The byte log also cannot distinguish a LIVE bottom-of-screen dialog from ANSWERED scrollback.
# That forced the old `resume_picker` / trailing-budget heuristics — which then suppressed the
# real thing, because the compaction dialog IS a resume picker (same three options). A fix for
# false positives had created a false negative.
#
# pyte reconstructs the ACTUAL screen: words have real spaces, and a dismissed dialog simply
# isn't there (`render_snapshot` already strips dismissed alt-screen dialogs). Rendering costs
# ~55-95ms on a 64KB log, so it is gated three ways: callers only ask for agents that are
# in_turn, a cheap space-collapsed pre-gate skips the render when no prompt marker exists
# anywhere in the buffer (the overwhelmingly common case), and the result is memoized per
# unchanged buffer for a few seconds.
# The pre-gate must be a SUPERSET of every phrase _terminal_awaiting_input_hint can fire on —
# it is only allowed to skip the render when NO prompt of any kind could possibly match. Written
# space-collapsed, because that is the form it is tested against (and because claude's TUI emits
# no spaces anyway). Miss a family here and you silently reintroduce the invisible-blocked bug for
# it: a "…I need a decision… Say the word" prompt has no menu cursor and no "Enter to confirm".
_PROMPT_MARKER_RE = re.compile(
    r"(\(y/n\)|\[y/n\]|\by/n\b|yes/no|areyousure|overwrite\?|password:|passphrase:"
    r"|entertoconfirm|pressenter|pressanykey|usearrow"
    r"|tellmewhich|needadecision|needdecision|whichoption|whichone|chooseone|chooseanoption|saytheword"
    r"|❯|›|▶)",
    re.I,
)
_PROMPT_HINT_TTL_SECONDS = 5.0
_PROMPT_HINT_CACHE: dict[str, tuple[str, float, str]] = {}


def _terminal_prompt_hint_from_raw(cache_key: str, raw: Any, cols: Any = 0) -> str:
    """Awaiting-input hint derived from the reconstructed SCREEN of a raw PTY log."""
    text = str(raw or "")
    if not text:
        return ""
    # Cheap pre-gate: collapse whitespace the way the escape-painted screen already is, and
    # look for ANY prompt marker. No marker anywhere -> the agent cannot be at a prompt -> skip
    # the expensive reconstruction entirely.
    if not _PROMPT_MARKER_RE.search(re.sub(r"\s+", "", _ANSI_RE.sub("", text))):
        return ""
    now = time.monotonic()
    digest = str(len(text)) + ":" + str(hash(text[-8192:]))
    cached = _PROMPT_HINT_CACHE.get(cache_key)
    if cached and cached[0] == digest and cached[1] > now:
        return cached[2]
    try:
        screen = _render_terminal_snapshot(text, int(cols or 0) or 100, 40)
    except Exception:
        screen = text  # pyte absent/failed: degrade to the old behaviour rather than lie
    hint = _terminal_awaiting_input_hint(screen)
    _PROMPT_HINT_CACHE[cache_key] = (digest, now + _PROMPT_HINT_TTL_SECONDS, hint)
    return hint


async def _agent_awaiting_input(db, agent_id: str) -> bool:
    """WS-5 (2026-06-17): True when the agent's live console tail looks like it is
    awaiting operator input/a decision (the `_terminal_awaiting_input_hint` signal).

    This is the engine input that makes `blocked` reachable under status_engine=new:
    derive() returns `blocked` for `in_turn AND live AND awaiting_input`. Callers gate
    the call on in_turn (a turn must be in flight for `blocked` to apply), so the
    terminal read happens only for the few agents currently mid-turn. Both StatusInputs
    build sites (_gather_status_inputs and the _compute_live_status_cache byproduct)
    call THIS helper so they derive the same value (the byproduct-parity promise)."""
    if db is None:
        return False
    try:
        row = await (await db.execute(
            """
            SELECT output, cols, runtime FROM terminal_sessions
            WHERE agent_id = ?
              AND status IN ('starting','attached','running','active','idle','recovering')
              AND id NOT LIKE 'vterm_%'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (agent_id,),
        )).fetchone()
    except Exception:
        return False
    if not row:
        return False
    # The screen patterns below model Claude Code's interactive permission,
    # resume, and compaction prompts. Hermes/Codex/Pi terminal output includes
    # the model's own prose; phrases such as "which option" or "say the word"
    # there are ordinary output, not proof that the harness is waiting for an
    # operator. Their controllers report turn state through native events.
    if _normalize_runtime(str(row["runtime"] or "")) != "claude-code":
        return False
    keys = row.keys()
    return bool(_terminal_prompt_hint_from_raw(
        f"agent:{agent_id}",
        row["output"] if "output" in keys else "",
        row["cols"] if "cols" in keys else 0,
    ))


# _terminal_idle_prompt_hint moved to service/reconcilers/terminal_runs.py in v0.5.3.


# class _WorkerLiveness moved to service/api_core/channel_delivery.py in v0.5.4
# with _worker_liveness_for, which returns it.


# How long a claimed spawn may report `starting` before it stops getting the benefit of the doubt.
#
# THE WINDOW IS THE POINT. Without it, this morning's genuinely-broken restart — spawn `running`, no
# terminal, ever — would have rendered as `starting` indefinitely, which is worse than the
# `available` it replaced: it turns a visible fault into a reassuring animation.
#
# IT MUST MATCH THE IN-FLIGHT SUPPRESSOR, and my first cut did not. I picked 180s from measured boot
# times while `_has_pending_or_booting_spawn_request` has counted a `running` spawn as in-flight for
# 300s since the 2026-07-02 Bug D fix. Between those two numbers the display said `available` —
# which PROMISES a cold-start on the next send — while the dispatcher was still refusing to start a
# second one. Caught in review; that contradiction is the precise class this status was added to
# remove, so the two are now one number with one reason.
#
# Change them together or not at all: a display that expires before the mechanism invites the
# operator to send into a suppressed window, and one that expires after keeps a broken spawn looking
# hopeful past the point anything is still trying.
# SPAWN_INFLIGHT_WINDOW_SECONDS moved to service/api_core/managed_env.py in v0.5.4 with both its readers.
# SPAWN_STARTING_WINDOW_SECONDS moved to service/api_core/managed_env.py in v0.5.4 with both its readers.


# _managed_spawn_is_starting moved to service/api_core/managed_env.py in v0.5.4.


# _managed_console_is_booting moved to service/api_core/managed_env.py in v0.5.4.


# _worker_liveness_for moved to service/api_core/channel_delivery.py in v0.5.4.



async def _gather_status_inputs(db, agent_row, *, settings=None) -> StatusInputs:
    """Build a StatusInputs from the SAME live signals the legacy derivation reads.

    No new derivation logic — just adapts existing signals (agent_status_state
    turn flags, _has_live_worker_for, _managed_owning_environment_row /
    _environment_effective_status for env reachability, _resident_bridge_is_fresh
    for resident liveness) into the engine's pure input contract. status v2.
    """
    settings = settings or await _load_settings(db)
    aid = agent_row["id"]
    mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    st = await (await db.execute(
        "SELECT in_turn, awaiting_input, last_event, last_event_at FROM agent_status_state WHERE agent_id=?", (aid,))).fetchone()
    in_turn = bool(st and st["in_turn"])
    awaiting_stored = bool(st and st["awaiting_input"])
    # status v2 (Fix B, 2026-06-05): in_turn staleness backstop. The OLD engine
    # clamps a stuck `working` via TURN_BUSY_BACKSTOP_SECONDS, but the NEW engine
    # had NO ceiling on in_turn — so an agent with a turn-START signal but a
    # DROPPED/absent turn-END (e.g. resident hermes, which has a start hook but no
    # end hook) would latch `working` forever. Treat in_turn as ended once the
    # row's last_event_at is older than the same backstop (dropped-event safety).
    if in_turn:
        last_event_epoch = _iso_to_epoch(st["last_event_at"] if st else "")
        if last_event_epoch and (
            datetime.now(timezone.utc).timestamp() - last_event_epoch
        ) > TURN_BUSY_BACKSTOP_SECONDS:
            in_turn = False
    # PURE-EVENT (2026-06-19): the turn-end GRACE was removed from BOTH status paths — this
    # WS-push path (_gather_status_inputs) and the byproduct/poll path (_compute_live_status_cache).
    # It held in_turn for 20s after a turn-END to mask a managed wrapper's premature Stop, but
    # that 20s time-decay is exactly what the operator rejects, and leaving it ONLY here made the
    # pushed status disagree with the polled status for 20s. The flap is fixed at the SOURCE (fast
    # bridge turn detectors re-assert a premature clear within a tick); derive() stays pure-event.
    # DISABLED = explicit stop OR wake disabled (launch_mode='none' — the operator's "Stop
    # wake"). The engine only knew 'stopped' (2026-06-12 audit): wake-disabled agents served
    # `available` under status_engine=new — inviting sends that can never wake them — while
    # the legacy path correctly said offline (Phase 3: offline = explicit disable). This was
    # the bulk of the old/new status-disagreement log noise (ef-* fleet).
    disabled = (
        str(agent_row["status"] or "").lower() == "stopped"
        or str(agent_row["launch_mode"] or "").lower() == "none"
    )
    # Compute the live-worker signal first (it gates the console-working lease below), then
    # fold the worker-gated spinner lease into in_turn — MUST match the byproduct path so the
    # WS-push status equals the served poll status (bughunt: lease was missing here).
    console_lease = await _console_working_lease_fresh(db, aid)
    if mode == "managed":
        env_row = await _managed_owning_environment_row(db, agent_row, resolved_environment_id="")
        env_reachable = _managed_env_reachable(agent_row, env_row, settings)
        worker_present = await _has_live_worker_for(db, agent_row, settings=settings)
        live_signal = worker_present
    else:
        worker_present = await _resident_bridge_is_fresh(db, agent_row,
            lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150))
        live_signal = worker_present
    in_turn = in_turn or (console_lease and live_signal)
    # WS-5 (2026-06-17): make `blocked` reachable under new — an in-turn agent whose console
    # tail looks like it awaits operator input derives `blocked`. Gate on the (lease-folded)
    # in_turn so the terminal read is bounded to agents currently mid-turn.
    awaiting = awaiting_stored or (in_turn and await _agent_awaiting_input(db, aid))
    if mode == "managed":
        # WS-12 (2026-06-17): a managed console that is up but whose sidecar hasn't claimed yet
        # is BOOTING → display `online` (parity with the legacy display-only promotion). Only
        # relevant when it would otherwise read `available` (no worker, env reachable).
        console_booting = (
            not worker_present and env_reachable
            and await _managed_console_is_booting(db, aid)
        )
        config_defect = ""
        if not worker_present and env_reachable and not console_booting:
            config_defect = await _agent_config_defect(db, agent_row, mode)
        # The EARLIER boot phase than console_booting: a claimed spawn whose worker has not appeared
        # yet, bounded by SPAWN_STARTING_WINDOW_SECONDS so a spawn that never produces one stops
        # claiming to be on its way. Only computed when it could change the answer.
        spawn_starting = (
            not worker_present and env_reachable and not console_booting and not config_defect
            and await _managed_spawn_is_starting(db, aid)
        )
        return StatusInputs(mode=mode, alive=worker_present, in_turn=in_turn, awaiting_input=awaiting,
                            worker_present=worker_present, env_reachable=env_reachable, disabled=disabled,
                            bridge_stale=False, has_live_session=worker_present,
                            console_booting=console_booting, config_defect=config_defect,
                            spawn_starting=spawn_starting)
    # Phase I flip parity: a resident in a `*-missing-handle` wake-mode (no usable wake
    # handle — e.g. resident hermes with no live gatewayUrl, resident codex/pi without a
    # sessionHandle) CANNOT be woken, so it reads `stale` even if a bridge looks fresh
    # (mirrors the legacy resident missing-handle gate; matches the dashboard's red dot).
    missing_handle = str(_agent_wake_mode(agent_row) or "").endswith("-missing-handle")
    return StatusInputs(mode=mode, alive=worker_present, in_turn=in_turn, awaiting_input=awaiting,
                        worker_present=worker_present, env_reachable=True, disabled=disabled,
                        bridge_stale=(not worker_present) or missing_handle, has_live_session=worker_present,
                        console_booting=False,
                        config_defect=await _agent_config_defect(db, agent_row, mode, missing_handle=missing_handle))


async def _agent_config_defect(db, agent_row, mode: str, *, missing_handle: bool = False) -> str:
    """Why this identity can NEVER be started, or "" if it can.

    Operator-requested 2026-08-03. Both fallthroughs this feeds used to report a state that
    quietly promises recovery: a managed identity with nothing to spawn from reported
    `available`, which tells the operator "just send to it and it will cold-start", and a
    resident with no wake handle reported `offline`, which reads as "not here right now".
    Both are false, in the direction that costs the most — the operator hunts a delivery bug
    that does not exist. This returns the DEFECT so status can say so instead.

    Deliberately narrow: only conditions under which starting is structurally impossible.
    A status that cried misconfigured on a recoverable agent would be worse than the promise it
    replaces — and the first cut of this DID. It also flagged a managed agent with no spawn spec,
    which is wrong: the cold-start path synthesises a spawn request from the environment, so a
    spec-less agent starts fine. An existing parity test caught it. What remains are the two
    conditions that no start path can route around: an unlaunchable runtime, and a resident with
    no wake handle.
    """
    if mode != "managed":
        if missing_handle:
            return f"no usable wake handle (wakeMode={_agent_wake_mode(agent_row) or 'unknown'})"
        return ""
    runtime = _normalize_runtime(agent_row["runtime"] or "") if "_normalize_runtime" in globals() else str(agent_row["runtime"] or "").strip().lower()
    if runtime not in _LAUNCHABLE_RUNTIMES:
        return f"runtime {runtime or '(unset)'!r} cannot be launched — no adapter can start it"
    return ""


async def engine_status(db, agent_row, *, settings=None) -> str:
    """status v2: serve one of VALID_STATUSES from the pure engine."""
    return derive(await _gather_status_inputs(db, agent_row, settings=settings))


async def _compute_live_status_cache(db, agent_row, *, settings: Optional[dict[str, Any]] = None, now: Optional[str] = None) -> dict[str, Any]:
    settings = settings or await _load_settings(db)
    now = now or _now()
    manual_status = str(agent_row["status"] or "").strip().lower()
    if manual_status in _MANUAL_STATUSES:
        return {
            "status": manual_status,
            "reason": _row_status_note(agent_row),
            "environment_id": "",
            "session_id": "",
            "terminal_id": "",
            "active_run_id": "",
            "refresh_after": "9999-12-31T23:59:59Z",
            "updated_at": now,
        }
    session_row = await _current_agent_session_row(db, agent_row["id"])
    active_run = await _current_active_run_row(db, agent_row["id"])
    channel_pending_reply_run = await _current_channel_awaiting_reply_run_row(db, agent_row["id"])
    # Authoritative mid-turn signal pushed by the bridge (contract). Fresh
    # turn_busy=1 means the runtime is executing a turn right now → working,
    # even when the dispatch row is delivered/ambiguous. Stale (no refresh
    # within TURN_BUSY_STALE_SECONDS) is treated as not-busy.
    turn_busy = False
    turn_runtime = ""
    turn_updated_at = ""
    # Plan 4 task 12 (2026-05-25): `ready` is the bridge-pushed
    # handshake-complete signal. It remains an internal readiness bit; the
    # public idle-live status is `online` so operators do not see both
    # `ready` and `available` as competing positive states.
    turn_state_ready = False
    try:
        _tb = await (await db.execute(
            "SELECT turn_busy, turn_runtime, turn_updated_at, ready FROM agent_turn_state WHERE agent_id = ?",
            (agent_row["id"],),
        )).fetchone()
        if _tb:
            if int(_tb["turn_busy"] or 0) == 1:
                _age = datetime.now(timezone.utc).timestamp() - _iso_to_epoch(str(_tb["turn_updated_at"] or ""))
                # WS5 Task 5.2/5.3: STATUS staleness uses the LONG backstop. The
                # turn-END event (POST /turn-end) is the primary clear; this window
                # only catches a DROPPED event, so it sits at the single long
                # wall-clock ceiling rather than racing the re-pulse cadence.
                if _iso_to_epoch(str(_tb["turn_updated_at"] or "")) and _age <= TURN_BUSY_BACKSTOP_SECONDS:
                    turn_busy = True
                    turn_runtime = str(_tb["turn_runtime"] or "").strip()
                    turn_updated_at = str(_tb["turn_updated_at"] or "").strip()
            # PURE-EVENT (2026-06-19): the turn-end GRACE (#224, 20s) was REMOVED. It held
            # `working` for 20s after turn_busy cleared to mask a managed claude's premature/
            # duplicate Stop hooks — a TIME-BASED hold that (a) stacked on the hermes bridge's
            # 9s idle-debounce to show "working" ~30s after a real idle (operator-reported), and
            # (b) is exactly the time-decay the status engine must not have. The flap is now
            # fixed AT THE SOURCE: the bridge turn detectors (hermes gateway / claude transcript)
            # only clear turn_busy on EVENT-confirmed end, and run fast enough to re-assert a
            # premature clear within a tick. Status here is pure-event: turn_busy=1 (within the
            # far 30-min wedged-bridge backstop) AND live → working; otherwise online.
            try:
                turn_state_ready = int(_tb["ready"] or 0) == 1
            except (IndexError, KeyError):
                # Pre-migration row (column absent on a foreign DB schema).
                turn_state_ready = False
    except Exception:
        turn_busy = False
        turn_state_ready = False
    # Console-working lease (2026-06-05): a fresh spinner-gated lease is the managed-claude
    # "working" signal the per-completed-message transcript can't see (a long thinking phase
    # shows the last ENDED message). Read it HERE, but fold it into turn_busy / the v2 in_turn
    # input only AFTER worker liveness is known (below) — gated on a live worker so it can
    # never manufacture `working` for a dead/available agent (additive-only contract).
    console_working_lease = False
    console_lease_iso = ""
    subagents_active = False
    try:
        _cw = await (await db.execute(
            "SELECT working_at, subagents_at FROM agent_console_signal WHERE agent_id = ?",
            (agent_row["id"],),
        )).fetchone()
        if _cw:
            _cw_iso = str(_cw["working_at"] or "").strip()
            _seen = _iso_to_epoch(_cw_iso)
            if _seen and datetime.now(timezone.utc).timestamp() - _seen <= CONSOLE_WORKING_LEASE_SECONDS:
                console_working_lease = True
                console_lease_iso = _cw_iso
            # Subagents mini-tag (2026-06-11): the bridge stamps subagents_at while the
            # claude background-agents manager shows a RUNNING row. Same TTL as the lease.
            _sa_seen = _iso_to_epoch(str(_cw["subagents_at"] or "").strip()) if "subagents_at" in _cw.keys() else 0
            if _sa_seen and datetime.now(timezone.utc).timestamp() - _sa_seen <= CONSOLE_WORKING_LEASE_SECONDS:
                subagents_active = True
    except Exception:
        console_working_lease = False
    runtime_state = _json_loads_or(agent_row["runtime_state"], {})
    environment_id = str((session_row["environment_id"] if session_row else "") or runtime_state.get("environmentId") or "").strip()
    env_row = None
    env_status = ""
    env_bridge_id = ""
    env_last_seen = ""
    if environment_id:
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        env_last_seen = str((env_row["last_seen"] if env_row else "") or "").strip()
        env_status = _environment_effective_status(env_row, offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90))) if env_row else "offline"
        env_bridge_id = str((env_row["bridge_id"] if env_row else "") or "").strip()
    session_id = str((session_row["id"] if session_row else "") or "").strip()
    terminal_id = str((session_row["terminal_id"] if session_row and "terminal_id" in session_row.keys() else "") or "").strip()
    session_status = str((session_row["status"] if session_row else "") or "").strip().lower()
    terminal_status = str((session_row["terminal_status"] if session_row and "terminal_status" in session_row.keys() else "") or "").strip().lower()
    session_bridge_id = str((session_row["owner_bridge_id"] if session_row and "owner_bridge_id" in session_row.keys() else "") or "").strip()
    agent_last_seen = str(agent_row["last_seen"] or "").strip()
    # A live session stays reachable across bridge restarts: a new bridge
    # instance for the same environment re-adopts it on the next dispatch
    # claim, and dispatch routing safety is enforced separately by the
    # superseded-bridge checks. So a bridge-instance id change must NOT by
    # itself mark a running session offline -- only genuine env-down or
    # heartbeat staleness should. Stale "running" rows are still caught by
    # the env-offline branch below and the heartbeat-freshness else-branch.
    live_session = session_status in _LIVE_SESSION_STATUSES
    # New status taxonomy (persistent-worker model — see
    # docs/plans/persistent-worker-status-taxonomy.md).
    # `has_live_worker` discriminates `available` (env online, no
    # worker) from `online` (worker alive, idle). The "worker" is
    # whichever runtime process actually serves dispatches:
    #   - Virtual rpc child (pi managed, hermes managed) → a
    #     terminal_session row with command in VIRTUAL_RPC_COMMAND_SET
    #     and active status.
    #   - Wrapper PTY (claude-aify, codex-aify, hermes-aify, pi-aify,
    #     omp-aify, opencode wrapper) → terminal_session whose command
    #     contains "-aify" or "opencode", with active status.
    #   - Resident without any terminal row → fall back to live_session
    #     (operator launched the wrapper outside the dashboard's
    #     terminal_sessions tracking).
    # A live agent_session ALONE is NOT enough — the bridge keeps the
    # row across worker restarts (graph-tech-lead symptom: Console
    # stopped, session row stale-running, agent should be `available`
    # not `online`).
    agent_session_mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    # status v2 (2026-06-04): capture the raw resident bridge-freshness ONCE so the
    # StatusInputs byproduct assembled below can reuse it without a second
    # _resident_bridge_is_fresh call. Mirrors _gather_status_inputs, which calls it
    # UNGATED for residents; the legacy resident_bridge_stale below stays gated on
    # the resident-run capability exactly as before (behavior-preserving).
    resident_bridge_fresh: Optional[bool] = None
    if agent_session_mode == "resident":
        resident_bridge_fresh = await _resident_bridge_is_fresh(
            db,
            agent_row,
            lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150),
        )
    resident_bridge_stale = False
    if agent_session_mode == "resident" and "resident-run" in _row_capabilities(agent_row):
        resident_bridge_stale = not resident_bridge_fresh
    # fix/resident-hermes-status (2026-06-02): a resident agent whose wake-mode is
    # a `*-missing-handle` mode has NO usable wake handle (resident hermes with no
    # usable gatewayUrl; resident codex/opencode/pi with no sessionHandle) — it
    # cannot be woken at all, so it is NOT `available`. It must read `stale`,
    # CONSISTENT with the dashboard dot, which already derives a red/unreachable
    # dot from the non-live-wake wake-mode (operator-reported `available`+red+
    # "Hermes missing handle" split). NOTE: the resident_bridge_stale gate above
    # is itself gated on `"resident-run" in _row_capabilities(...)`, and
    # _row_capabilities STRIPS resident-run for a hermes with no gatewayUrl — so a
    # missing-handle resident never reaches that gate and would otherwise fall
    # through to `available`. This flag closes that hole at the same liveness
    # altitude. A genuinely-live resident (fresh bridge + usable handle →
    # `*-live`/`-thread-resume`) is unaffected. Excludes `presence-only`
    # (opencode/pi resident) and inbox/`message-only` agents, which are not
    # wake-handle-backed targets and have their own taxonomy.
    resident_missing_handle = False
    if agent_session_mode == "resident":
        _wake_mode = _agent_wake_mode(agent_row)
        if _wake_mode.endswith("-missing-handle"):
            resident_missing_handle = True
            resident_bridge_stale = True
    # has_live_worker (+ the two channel-sidecar reason flags) is now decided by
    # the SHARED _worker_liveness_for helper so the legacy derivation and the
    # event engine (_gather_status_inputs → _has_live_worker_for) can never
    # disagree on worker liveness. Behavior-preserving extraction — same inputs
    # (agent_session_mode, live_session), same result.
    _worker_live = await _worker_liveness_for(
        db, agent_row, agent_session_mode=agent_session_mode, live_session=live_session
    )
    has_live_worker = _worker_live.has_live_worker
    channel_managed_no_sidecar = _worker_live.channel_managed_no_sidecar
    channel_managed_no_console = _worker_live.channel_managed_no_console
    # FIX B (2026-06-02): a MANAGED agent can only be spawned/hosted by its OWNING
    # environment bridge. If that env bridge is offline/stale, the agent is
    # effectively offline — even when a surviving detached delivery loop keeps a
    # fresh sidecar/lease/heartbeat (which would otherwise compute `online`). The
    # operator killed the `aify-comms` env bridge and managed agents stayed
    # `available`/`online` for exactly this reason: the env-bound offline branch
    # below only fires when `environment_id` resolved from a LIVE session row /
    # runtime_state, both absent once the worker died. This gate resolves the
    # STORED owning environment (runtime_config.environmentId / machine_id+runtime
    # match) and hard-forces offline, short-circuiting the online/available
    # derivation. Resident agents are EXCLUDED — their liveness is the resident
    # bridge, not the env bridge — so a down env bridge must not force them offline.
    managed_env_bridge_offline = False
    if agent_session_mode == "managed":
        owning_env_row = await _managed_owning_environment_row(
            db, agent_row, resolved_environment_id=environment_id
        )
        if owning_env_row is not None:
            owning_env_status = _environment_effective_status(
                owning_env_row,
                offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
            )
            if owning_env_status not in {"online", "degraded"}:
                managed_env_bridge_offline = True
                # Bind environment_id so the reason/offline branch below and the
                # cache row reflect the resolved owning environment.
                if not environment_id:
                    environment_id = str(owning_env_row["id"] or "").strip()
                    env_status = owning_env_status
                    env_last_seen = str((owning_env_row["last_seen"] or "")).strip()
    if managed_env_bridge_offline:
        # Owning env bridge is down → hard offline regardless of any surviving loop.
        has_live_worker = False
        effective_status = "offline"
    elif has_live_worker:
        # A live worker that is not handling a turn is public `online`.
        # `turn_state_ready` remains useful internally for readiness and cache
        # invalidation, but is not a separate user-facing agent status.
        effective_status = "online"
    elif environment_id and env_status not in {"online", "degraded"}:
        # An env IS bound but it's unreachable → offline. Unbound agents
        # (no environment_id yet) fall through to "available" — they can
        # still receive a message, the dispatch path resolves the env at
        # claim time.
        effective_status = "offline"
    else:
        effective_status = "available"
    # Fold the console-working lease into turn_busy now that worker liveness is known.
    # Gated on has_live_worker so it can NEVER manufacture `working` for a dead/available
    # agent — only a live managed worker showing its spinner reads `working` (the
    # turn_busy branch below). Additive: it never clears turn_busy.
    if console_working_lease and has_live_worker and not turn_busy:
        turn_busy = True
        if not turn_runtime:
            turn_runtime = "claude-code"
    reason = ""
    awaiting_reply = False  # set True when the agent is idle but owes a channel reply
    terminal_input_hint = ""
    if (
        _normalize_runtime(str(agent_row["runtime"] or "")) == "claude-code"
        and terminal_id
        and (active_run or (agent_session_mode == "managed" and has_live_worker))
    ):
        try:
            terminal_row = await (await db.execute(
                "SELECT output, cols FROM terminal_sessions WHERE id = ?",
                (terminal_id,),
            )).fetchone()
            terminal_input_hint = _terminal_prompt_hint_from_raw(
                f"term:{terminal_id}",
                terminal_row["output"] if terminal_row else "",
                (terminal_row["cols"] if terminal_row and "cols" in terminal_row.keys() else 0),
            )
        except Exception:
            terminal_input_hint = ""
    active_run_runtime = _normalize_runtime(str(active_run["runtime"] or "")) if active_run else ""
    active_run_mode = str(active_run["dispatch_mode"] or "").strip().lower() if active_run else ""
    active_run_terminal_missing = (
        active_run
        and active_run_mode == "terminal"
        and (not terminal_id or terminal_status not in _TERMINAL_ACTIVE_STATUSES)
    )
    effective_status, reason, awaiting_reply = await _decide_effective_status(
        active_run,
        active_run_terminal_missing,
        agent_row,
        agent_session_mode,
        channel_managed_no_console,
        channel_managed_no_sidecar,
        channel_pending_reply_run,
        db,
        env_bridge_id,
        env_status,
        environment_id,
        has_live_worker,
        live_session,
        managed_env_bridge_offline,
        resident_bridge_stale,
        session_bridge_id,
        session_status,
        terminal_input_hint,
        terminal_status,
        turn_busy,
        turn_runtime,
        effective_status,
        reason,
        awaiting_reply,
    )
    # NOTE (2026-06-05): a managed agent whose last session ended FAILED stays `available` by
    # design — it lazy-respawns on the next send (genuinely available-to-retry, NOT blocked; see
    # test_managed_codex_online_from_fresh_wrapper_child_bridge). The originally-reported
    # "stopped · Console attached" was a TRANSIENT teardown race during a hermes resume error,
    # removed at the root by the DB-validated resume fix (5c1617a); the dashboard console label
    # is the honest surface (never "attached" for a dead session — Dashboard Next).
    refresh_after = _status_refresh_after(
        agent_last_seen,
        env_last_seen,
        liveness_seconds=int(settings.get("agent_liveness_seconds", 90) or 90),
        env_offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
    )
    # When `working` is driven by a fresh turn_busy (NOT an active run, which has
    # its own lifecycle), clamp refresh_after to the turn-busy BACKSTOP window so a
    # DROPPED turn-end event self-heals at the single long ceiling (~15m) instead of
    # waiting out the 5-30min heartbeat windows. WS5 Task 5.2/5.3: the normal off-
    # working transition is the turn-END EVENT (which invalidates the cache
    # immediately via /turn-end), so this clamp is purely the dropped-event
    # backstop. `active_run` working is intentionally left untouched.
    if effective_status == "working" and turn_busy and not active_run and turn_updated_at:
        busy_deadline = _iso_add_seconds(turn_updated_at, TURN_BUSY_BACKSTOP_SECONDS)
        if busy_deadline:
            refresh_after = min([v for v in (refresh_after, busy_deadline) if v])
    # (Turn-end grace removed 2026-06-19 — pure-event; see the turn_busy block above.)
    # M2: when `working` is driven by the console-working lease (turn_updated_at is unset,
    # so the backstop clamp above is skipped), clamp refresh_after to the lease TTL so the
    # cache self-expires when the spinner stops — the bridge stops POSTing, so nothing else
    # forces a recompute, and the cached `working` would otherwise persist to the next
    # heartbeat window (minutes) rather than the 12s lease.
    if effective_status == "working" and console_working_lease and console_lease_iso:
        lease_deadline = _iso_add_seconds(console_lease_iso, CONSOLE_WORKING_LEASE_SECONDS)
        if lease_deadline:
            refresh_after = min([v for v in (refresh_after, lease_deadline) if v])
    # POLL-LOAD FIX (2026-06-18): a settled `offline` agent computes refresh_after from
    # agent_last_seen + liveness — ANCIENT for a long-dead agent, so it is PERMANENTLY expired
    # and gets re-derived + re-persisted on EVERY roster poll (GET /agents | /sessions), a
    # write storm that saturated the single SQLite writer (sustained `database is locked`).
    # An offline agent needs no poll-driven recompute: its status only changes via an explicit
    # cache-invalidating event (heartbeat/turn/operator action -> _invalidate_agent_live_state).
    # Push refresh_after to a moderate future horizon so the hot read path serves cache; the
    # reconcile sweep still re-validates it each horizon (env-return safety), recovery on any
    # real event is immediate via invalidation. (`stopped`/manual already short-circuit at the
    # top with a 9999 horizon.)
    if effective_status == "offline":
        offline_revalidate = int(settings.get("agent_offline_revalidate_seconds", OFFLINE_CACHE_REVALIDATE_SECONDS) or OFFLINE_CACHE_REVALIDATE_SECONDS)
        horizon = _iso_add_seconds(now, max(60, offline_revalidate))
        if horizon:
            refresh_after = horizon
    # status v2 (2026-06-04): assemble the engine's StatusInputs from the raw
    # signals THIS function already computed, so _refresh_agent_live_state can
    # derive the `new` status with a PURE derive() call instead of re-running the
    # full _gather_status_inputs double-gather (the 10x idle-CPU regression). This
    # MUST produce the same StatusInputs _gather_status_inputs does — the field
    # semantics below mirror it exactly (see _gather_status_inputs).
    #   - mode/disabled: same source rows.
    #   - in_turn/awaiting_input: one cheap indexed agent_status_state lookup (the
    #     SAME table _gather_status_inputs reads; the legacy derivation above uses
    #     agent_turn_state.turn_busy instead, so this single query is required).
    #   - worker_present (managed): the already-computed `has_live_worker` local —
    #     the SHARED _worker_liveness_for result, identical to _has_live_worker_for,
    #     so the expensive worker re-scan is eliminated.
    #   - env_reachable (managed): resolved exactly as _gather_status_inputs (owning
    #     env row with resolved_environment_id="" -> effective status in online/
    #     degraded). A cheap indexed env lookup, NOT the expensive worker re-scan.
    #   - resident liveness: the `resident_bridge_fresh` local captured above (the
    #     SAME _resident_bridge_is_fresh call _gather_status_inputs makes, computed
    #     once and reused).
    _si_st = await (await db.execute(
        "SELECT in_turn, awaiting_input, last_event_at FROM agent_status_state WHERE agent_id=?",
        (agent_row["id"],),
    )).fetchone()
    # M-B parity (2026-06-05): mirror the _gather_status_inputs in_turn staleness backstop
    # (Fix B) here too. This byproduct is the SERVED path under status_engine=new; without
    # the clamp a DROPPED/absent turn-END would latch `working` here forever while the
    # authoritative _gather_status_inputs would correctly clear it past the backstop — so the
    # "MUST produce the same StatusInputs" promise above would be violated for stale in_turn.
    _si_raw_in_turn = bool(_si_st and _si_st["in_turn"])
    if _si_raw_in_turn:
        _si_last_event_epoch = _iso_to_epoch(_si_st["last_event_at"] if _si_st else "")
        if _si_last_event_epoch and (
            datetime.now(timezone.utc).timestamp() - _si_last_event_epoch
        ) > TURN_BUSY_BACKSTOP_SECONDS:
            _si_raw_in_turn = False
    # H1: the console-working lease must feed BOTH engines. The v2 engine reads in_turn from
    # agent_status_state (which the lease never writes), so OR the worker-gated lease in here
    # too — otherwise the feature is a no-op under status_engine=new. (The lease has its OWN
    # short TTL, so OR-ing it after the staleness clamp can't resurrect a truly-stale turn.)
    _si_in_turn = _si_raw_in_turn or (console_working_lease and has_live_worker)
    _si_awaiting = bool(_si_st and _si_st["awaiting_input"])
    # WS-5 parity: compute the awaiting-input signal via the SAME helper _gather_status_inputs
    # uses (NOT the legacy terminal_input_hint above, which keys on the bound terminal_id) so
    # both StatusInputs builders agree. Gated on _si_in_turn (blocked only applies mid-turn).
    if _si_in_turn and not _si_awaiting:
        _si_awaiting = await _agent_awaiting_input(db, agent_row["id"])
    # Mirrors _gather_status_inputs exactly (the byproduct-parity promise): disabled =
    # stopped OR wake disabled (launch_mode='none') — see the 2026-06-12 audit note there.
    _si_disabled = (
        str(agent_row["status"] or "").lower() == "stopped"
        or str(agent_row["launch_mode"] or "").lower() == "none"
    )
    if agent_session_mode == "managed":
        _si_env_row = await _managed_owning_environment_row(db, agent_row, resolved_environment_id="")
        _si_env_reachable = _managed_env_reachable(agent_row, _si_env_row, settings)
        # WS-12 parity: booting-console → display online (same helper as _gather_status_inputs).
        _si_console_booting = (
            not has_live_worker and _si_env_reachable
            and await _managed_console_is_booting(db, agent_row["id"])
        )
        status_inputs = StatusInputs(
            mode=agent_session_mode, alive=has_live_worker, in_turn=_si_in_turn,
            awaiting_input=_si_awaiting, worker_present=has_live_worker,
            env_reachable=_si_env_reachable, disabled=_si_disabled,
            bridge_stale=False, has_live_session=has_live_worker,
            console_booting=_si_console_booting,
        )
    else:
        _si_fresh = bool(resident_bridge_fresh)
        # Phase I flip parity (see _gather_status_inputs): a *-missing-handle resident → stale.
        _si_missing_handle = str(_agent_wake_mode(agent_row) or "").endswith("-missing-handle")
        status_inputs = StatusInputs(
            mode=agent_session_mode, alive=_si_fresh, in_turn=_si_in_turn,
            awaiting_input=_si_awaiting, worker_present=_si_fresh,
            env_reachable=True, disabled=_si_disabled,
            bridge_stale=(not _si_fresh) or _si_missing_handle, has_live_session=_si_fresh,
            console_booting=False,
        )
    # Subagents mini-tag (2026-06-11): surfaced through the reason string (the dashboard
    # already derives nuances like awaiting-reply from it) so no payload-shape change.
    if subagents_active and effective_status == "working":
        reason = f"{reason} Running subagents.".strip()
    return {
        "status": effective_status,
        "reason": reason,
        "awaiting_reply": awaiting_reply,
        "environment_id": environment_id,
        "session_id": session_id,
        "terminal_id": terminal_id,
        "active_run_id": str((active_run["id"] if active_run else "") or "").strip(),
        "refresh_after": refresh_after,
        "updated_at": now,
        "status_inputs": status_inputs,
    }














async def _refresh_agent_live_state(db, agent_id: str, *, settings: Optional[dict[str, Any]] = None, now: Optional[str] = None):
    row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not row:
        return None
    settings = settings or await _load_settings(db)
    cache = await _compute_live_status_cache(db, row, settings=settings, now=now)
    # status v2 flag-branch (2026-06-04). The served status is the cache `status`.
    # Under `status_engine=new` the event-driven engine becomes authoritative for
    # the served value; under `old` (default) the legacy derivation is unchanged.
    # Disagreements are always logged so the new engine can be validated before
    # the flip. Manual statuses (stop/disable) short-circuit the engine too — they
    # are operator overrides that both paths must honor identically.
    # Proof-based engine is the ONE authority (2026-06-18: the status_engine old|new flag is
    # gone). Manual statuses (stop/disable) are an operator override derive() already encodes
    # via the `disabled` input; we keep the short-circuit so a stopped agent never depends on
    # the rest of the input gather. The served status is derive() of the assembled inputs (a
    # PURE call on the byproduct _compute_live_status_cache already built — no second gather).
    if cache["status"] not in _MANUAL_STATUSES:
        try:
            _legacy_status = cache["status"]
            _derived = derive(cache["status_inputs"])
            # derive() is the ONE authority for the served status. `cache["reason"]`
            # (served as statusNote) was computed by the legacy cascade for the
            # legacy status; when derive() DISAGREES, that reason describes the
            # superseded status and contradicts what the operator sees (e.g. a
            # dead-worker-mid-turn: derive→"available" but reason="Active run: X").
            # Drop the stale reason on disagreement so the note never mismatches the
            # status. (Cosmetic-only: dispatch keys on worker_present, not reason.)
            if _derived != _legacy_status:
                cache["reason"] = ""
            cache["status"] = _derived
        except Exception:
            logger.exception("status derive failed for agent=%s; keeping computed status", agent_id)
    # Store in the in-memory cache — NOT the DB (was the write-storm source). No lock possible.
    _live_state_set(agent_id, cache)
    return cache










# _terminal_pi_idle_prompt_hint moved to service/reconcilers/terminal_runs.py in v0.5.3.




LIST_AGENTS_REFRESH_LIMIT = 8


async def _refresh_expired_agent_live_states(db, *, settings: Optional[dict[str, Any]] = None, agent_ids: Optional[list[str]] = None, limit: Optional[int] = None) -> int:
    """Recompute expired/missing live-status entries INTO THE IN-MEMORY CACHE. Returns how many
    were refreshed. No DB writes happen here anymore — the status cache lives in _LIVE_STATE_CACHE
    (2026-06-18), so there is nothing to commit and a read can never take SQLite's write lock.

    `limit` bounds the per-call recompute count (CPU only) for the hot GET /agents path; the
    reconcile sweep calls it unbounded (limit=None). Missing entries are refreshed first, then
    the oldest, so the most-stale agents recompute soonest under the cap."""
    settings = settings or await _load_settings(db)
    now = _now()
    if agent_ids:
        ids = [str(a or "").strip() for a in agent_ids if str(a or "").strip()]
    else:
        rows = await (await db.execute("SELECT id FROM agents")).fetchall()
        ids = [r["id"] for r in rows]
    # Order: missing-from-cache first, then by oldest refresh_after — so the most-stale recompute
    # soonest when `limit` caps the batch.
    def _sort_key(aid: str):
        entry = status_cache._LIVE_STATE_CACHE.get(aid)
        if not entry:
            return (0, "")
        return (1, str(entry.get("refresh_after") or ""))
    ids.sort(key=_sort_key)
    refreshed = 0
    for aid in ids:
        if limit is not None and refreshed >= limit:
            break
        if _live_state_fresh(aid, now=now) is None:
            await _refresh_agent_live_state(db, aid, settings=settings, now=now)
            refreshed += 1
    return refreshed


# _managed_environment_status moved to service/api_core/managed_env.py in v0.5.4.


# OWNED BY service/reconcilers/spawn_lifecycle.py since v0.5 slice 2. Imported rather than
# re-declared: two literals with the same value today is precisely how finding N7 happened.
# Caught by my own pre-tag review, which is the only reason it is not shipping duplicated.
from service.reconcilers.spawn_lifecycle import SPAWN_ORPHAN_GRACE_SECONDS  # noqa: E402
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES
from service.api_core.capabilities import (
    _has_codex_live_app_server,
    _row_capabilities,
)
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.claim_gating import _dispatch_source_message_ids
from service.api_core.reply_contract import _contract_reminder_due
from service.api_core.dispatch_buffer import (
    _DISPATCH_BUFFER_CAP,
    _append_pending_dispatch_body,
    _dispatch_buffer_full_hint,
)
from service.api_core.dispatch_hint import _dispatch_fix_hint

# Grace before a spawn is finalized because its bound terminal reached a terminal
# status. Deliberately SHORTER than SPAWN_ORPHAN_GRACE_SECONDS: that reaper infers
# death from a missing bridge heartbeat and must be generous, whereas this one has
# PROOF (the terminal row itself says stopped/failed) and the cost of waiting is that
# the dead worker keeps suppressing its own respawn. Long enough to lose a rebind
# race — a managed respawn can create the new terminal seconds before the session is
# re-pointed at it, and the guard below also requires that no live terminal shares
# the session.


















# _environment_supports_terminal moved to service/api_core/capabilities.py in v0.5.4.


# _environment_uses_windows_paths moved to service/api_core/capabilities.py in v0.5.4.


# _normalize_workspace_for_environment moved to service/api_core/workspace.py in v0.5.4.


# _workspace_root_for moved to service/api_core/workspace.py in v0.5.4.


# _workspace_for_environment moved to service/api_core/workspace.py in v0.5.4.








# _agent_session_to_dict moved to service/api_core/records.py in v0.5.4.


# _terminal_session_to_dict moved to service/api_core/records.py in v0.5.4.




# _terminal_control_to_dict moved to service/routers/terminals.py in v0.5.3.


# _trim_terminal_output moved to service/routers/terminals.py in v0.5.3, then on to
# service/api_core/terminal_output.py in v0.5.4.


def _row_get(row, key, default=None):
    """Safely fetch a field from either a dict or a sqlite3.Row."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return value if value is not None else default




async def _compute_agent_status(row, db=None):
    # Single source of truth: delegate to the live-state engine that
    # list_agents/get_agent already use, so write endpoints (heartbeat,
    # register, dispatch status) never disagree with the dashboard about
    # whether an agent is active/idle/offline. The db-less fallback below is
    # only the minimal heartbeat heuristic for callers without a connection.
    status = row["status"]
    if status in _MANUAL_STATUSES:
        return status
    if db is not None:
        # The CPU fix: the in-memory live-status entry is kept fresh by push events
        # (status-event ingest invalidates it) + the reconcile backstop, so a hot read
        # serves the cached status directly instead of recomputing on EVERY call (claim
        # deliverability / write endpoints / send preflight all funnel through here).
        # Only recompute when the cache entry is missing or expired.
        settings = await _load_settings(db)
        cached = _live_state_fresh(row["id"])
        if cached:
            return cached["status"]
        cache = await _refresh_agent_live_state(db, row["id"], settings=settings)
        if cache:
            return cache["status"]

    # Plan 4 (2026-05-25): db-less fallback. With a db, `_compute_live_status_cache`
    # already gates `online` on `has_live_worker` (wrapper PTY or RPC child) and
    # falls back to `available`. Without a db we cannot inspect terminal_sessions,
    # so a managed agent's persisted `status` column (likely `online`) is a lie
    # — degrade to `available` so the taxonomy stays honest. The db-less branch
    # is informational only (used by callers without a connection); db-backed
    # callers go through _compute_live_status_cache above, which DOES layer the
    # offline-via-stale-heartbeat check on top.
    session_mode = str(_row_get(row, "session_mode", "") or "")
    if session_mode == "managed":
        agent_id = _row_get(row, "id", "")
        if agent_id:
            has_terminal = await _has_live_terminal_session(db, agent_id)
            has_rpc = _has_live_rpc_controller(agent_id)
            if not has_terminal and not has_rpc:
                return "available"

    # Proof-based (2026-06-18): no idle/offline MINUTE decay. The only time element is the
    # short liveness window — heartbeat older than it = offline (gone). Otherwise online.
    try:
        last = datetime.fromisoformat(str(row["last_seen"] or "").replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - last
        liveness = int(DEFAULT_SETTINGS.get("agent_liveness_seconds", 90) or 90)
        if age > timedelta(seconds=liveness):
            status = "offline"
        elif status in ("idle", "active", "ready"):
            status = "online"  # legacy raw values are not engine statuses
    except Exception:
        pass
    return status











async def _periodic_pi_resident_flip_loop() -> None:
    """Background loop — every ~5s drain & flip pi resident agents.

    Best-effort: any exception during a tick is swallowed so the next
    tick retries. Wired into the FastAPI lifespan in service/main.py.
    """
    while True:
        try:
            await asyncio.sleep(5.0)
            await _drain_and_flip_pi_resident_agents()
        except asyncio.CancelledError:
            raise
        except Exception:
            # next tick retries
            pass


async def _get_recipient_info(db, recipient_id: str):
    if recipient_id == "dashboard":
        return {
            "status": "active",
            "unread": 0,
            "runtime": "dashboard",
            "machineId": "dashboard",
        }
    settings = await _load_settings(db)
    await _refresh_expired_agent_live_states(db, settings=settings, agent_ids=[recipient_id])
    c = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
    row = await c.fetchone()
    if not row:
        return None
    unread_map = await _get_unread_count_map(db, [recipient_id])
    dispatch_state = await _get_dispatch_state_map(db, [recipient_id])
    entry = _live_state_get(recipient_id) or {}
    return _agent_record_to_dict(
        row, entry.get("status") or row["status"], unread_map.get(recipient_id, 0),
        dispatch_state.get(recipient_id), live_reason=entry.get("reason"),
    )


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
    allow_busy_enqueue = allow_queue_busy or allow_steer

    for recipient_id in recipients:
        agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
        row = await agent_cursor.fetchone()
        if not row:
            not_started.append(_dispatch_fix_hint(recipient_id, None, "agent is not registered"))
            continue
        row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
        if _normalize_runtime(row["runtime"] or "") == "pi":
            runtime_state = _json_loads_or(row["runtime_state"], {})
            if runtime_state.get("pi_resident_pending_flip"):
                hint = _dispatch_fix_hint(
                    recipient_id,
                    row,
                    "agent is migrating from resident to managed (pi flip pending)",
                )
                hint["recipientStatus"] = "migrating"
                hint["fix"] = (
                    f'Agent "{recipient_id}" is migrating from resident to managed. '
                    "Retry after the drain loop flips the agent once active runs complete."
                )
                not_started.append(hint)
                continue
        if _normalize_session_mode(row["session_mode"] or "resident") == "resident":
            if not await _resident_bridge_is_fresh(db, row, lease_seconds=settings.get("resident_lease_seconds", 150)):
                hint = _dispatch_fix_hint(recipient_id, row, "resident bridge heartbeat is gone; restart the resident wrapper or switch to managed")
                hint["recipientStatus"] = "offline"
                not_started.append(hint)
                continue

        dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
        active = dispatch_state.get("activeRun")
        if active and await _discard_unusable_active_run(db, recipient_id, active):
            dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
        base_status = await _compute_agent_status(row, db)
        effective_status = _status_with_dispatch(base_status, dispatch_state)

        if effective_status in unavailable_statuses:
            hint = _dispatch_fix_hint(recipient_id, row, f'agent status is "{effective_status}"')
            hint["recipientStatus"] = effective_status
            not_started.append(hint)
            continue

        execution_mode, reason = _agent_execution_mode(row, settings=settings)
        if reason or not execution_mode:
            hint = _dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable")
            hint["recipientStatus"] = effective_status
            not_started.append(hint)
            continue

        environment_reason = await _managed_environment_unavailable_reason(db, row)
        if environment_reason:
            hint = _dispatch_fix_hint(recipient_id, row, environment_reason)
            hint["recipientStatus"] = "offline"
            not_started.append(hint)
            continue

        if dispatch_state.get("hasActiveRun"):
            active = dispatch_state.get("activeRun") or {}
            capabilities = _row_capabilities(row)
            if allow_steer and "steer" in capabilities:
                launchable.append((recipient_id, execution_mode))
                continue
            if allow_busy_enqueue:
                launchable.append((recipient_id, execution_mode))
                continue
            hint = _dispatch_fix_hint(recipient_id, row, "agent is working")
            hint["recipientStatus"] = "working"
            hint["activeRun"] = active
            active_suffix = f" on {active.get('runId')}" if active.get("runId") else ""
            hint["fix"] = (
                f'Agent "{recipient_id}" is already working{active_suffix}. '
                "Wait, interrupt the active run, or send with steer=true so aify can inject now when supported and queue/merge as the next-turn fallback otherwise."
            )
            not_started.append(hint)
            continue

        queued_runs = int(dispatch_state.get("queuedRuns") or 0)
        if queued_runs > 0:
            if allow_busy_enqueue:
                launchable.append((recipient_id, execution_mode))
                continue
            hint = _dispatch_fix_hint(recipient_id, row, "agent already has queued work")
            hint["recipientStatus"] = effective_status
            hint["queuedRuns"] = queued_runs
            hint["fix"] = (
                f'Agent "{recipient_id}" already has {queued_runs} queued run(s). '
                "Wait for the queue to drain, cancel stale runs, or send normally so aify can steer or merge when possible. Use queueIfBusy=true only when you intentionally want next-turn delivery."
            )
            not_started.append(hint)
            continue

        # WS5 Task 5.1b REVERSED (2026-06-02): the deaf-target fail-fast was
        # removed. A send to a managed sidecar-delivery target whose delivery loop
        # released/lost its claimer lease previously failed fast (ok:false, no run)
        # — but in live use that LOST messages to an agent that was merely
        # mid-restart (lease released then re-acquired moments later). The operator
        # reversed the decision: ALWAYS QUEUE here. The
        # `_reap_undeliverable_queued_runs` backstop reaper is now the sole safety
        # net — it fails a queued run only after it has been genuinely
        # undeliverable for the backstop window. `_managed_target_is_deaf` was
        # REMOVED in v0.5 after it was proven that nothing ever used it for the
        # status/deliverability classification it had been retained for; the lease
        # helpers and that backstop are what remain.
        launchable.append((recipient_id, execution_mode))

    return launchable, not_started












# _terminal_status_transition moved to service/routers/terminals.py in v0.5.3.




# class TerminalOutputWriteQueue moved to service/terminal_write_queue.py in v0.5.4,
# with its singleton. It is not an api_core leaf: it owns its own transaction.


# TERMINAL_OUTPUT_WRITES moved to service/terminal_write_queue.py in v0.5.4 —
# the declaration must stay beside the class so a second instance cannot appear.


async def flush_terminal_output_writes_for_tests() -> None:
    await TERMINAL_OUTPUT_WRITES.flush_all()

# _release_stale_console_owner_for_claim moved to service/routers/dispatch_messages/shared.py in v0.5.3.


# _release_stale_terminal_owner moved to service/api_core/terminal_ownership.py in v0.5.4.


# _active_terminal_for_agent moved to service/api_core/terminal_ownership.py in v0.5.4.



# _has_pending_or_booting_spawn_request moved to service/api_core/managed_env.py in v0.5.4.


# _has_claimable_steerable_run moved to service/routers/dispatch_messages/shared.py in v0.5.3.


# _select_online_environment_for_runtime moved to service/api_core/managed_env.py in v0.5.4.


# N8 (operator-reported twice: 2026-07-31 and 2026-08-07). `_coldstart_spawn_request_for_dispatch`
# returns a bare False for FIVE distinct causes, and every caller rendered ONE sentence for all of
# them: "No online environment can host managed <runtime> for this agent". On 2026-08-07 that
# sentence was shown while the environment was demonstrably online (last_seen 9s earlier) — the
# real cause was a spawn request already in flight. The message sent a competent agent to
# investigate the one thing that was fine.
#
# Reasons are appended to the caller's existing `warnings` list behind this prefix rather than
# changing the return type, so no call site's contract moves. Callers strip the prefix and show
# the reason; a caller that ignores warnings behaves exactly as before.
# COLDSTART_REFUSED_PREFIX moved to service/api_core/dispatch_text.py in v0.5.4 with
# _coldstart_refusal_message; the control plane is now one reader among several.


# _coldstart_refusal moved to service/api_core/dispatch_start.py in v0.5.4.

# _coldstart_refusal_message moved to service/api_core/dispatch_text.py in v0.5.4.



# _coldstart_spawn_request_for_dispatch moved to service/api_core/dispatch_start.py in v0.5.4.


# _ensure_managed_pty_for_dispatch moved to service/api_core/dispatch_start.py in v0.5.4.





_PRIORITY_ORDER = {"normal": 0, "high": 1, "urgent": 2}
# _MERGED_DISPATCH_HEADER moved to service/api_core/dispatch_text.py in v0.5.4.
# _MERGED_DISPATCH_FOOTER moved to service/api_core/dispatch_text.py in v0.5.4.
# _DISPATCH_BUFFER_CAP moved to service/api_core/dispatch_buffer.py in v0.5.4.


def _stronger_priority(left: str, right: str) -> str:
    left_key = str(left or "normal").strip().lower() or "normal"
    right_key = str(right or "normal").strip().lower() or "normal"
    return left_key if _PRIORITY_ORDER.get(left_key, 0) >= _PRIORITY_ORDER.get(right_key, 0) else right_key






# _render_pending_dispatch_item moved to service/api_core/dispatch_text.py in v0.5.4.


# _pending_dispatch_count moved to service/api_core/dispatch_text.py in v0.5.4.


# _build_pending_dispatch_subject moved to service/api_core/dispatch_text.py in v0.5.4.


# _append_pending_dispatch_body moved to service/api_core/dispatch_buffer.py in v0.5.4.


# _dispatch_buffer_full_hint moved to service/api_core/dispatch_buffer.py in v0.5.4.



# _discard_superseded_active_run moved to service/api_core/active_run_discard.py in v0.5.4.


# How many times a claimed-but-never-delivered run may be rescued before we accept that it
# is genuinely undeliverable and let it fail. Bounded on purpose: an unbounded prefer-recovery
# rule turns a dead run into an immortal one, which is the strand class DECISIONS.md warns
# about ("delivery gates read raw turn_busy, bounded by exactly one ceiling"). Counted from the
# run's OWN `requeued_orphaned_claim` events, so the bound needs no schema and survives a
# restart.
# UNDELIVERED_CLAIM_REQUEUE_LIMIT moved to service/api_core/recovery_writes.py in v0.5.4 with the
# rescue it bounds — the difference between a rescue and an infinite loop.


# _requeue_instead_of_failing_undelivered_claim moved to service/api_core/recovery_writes.py in v0.5.4.


# _fail_stale_active_run moved to service/api_core/active_run_discard.py in v0.5.4.


# _discard_unclaimable_active_run moved to service/api_core/active_run_discard.py in v0.5.4.


# _discard_unusable_active_run moved to service/api_core/active_run_discard.py in v0.5.4.





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
    queue_if_busy: bool = False,
    require_reply: bool = False,
    allow_merge: bool = True,
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
            if active_run and await _discard_unusable_active_run(db, recipient_id, active_run):
                active_state = await _get_dispatch_state_for_agent(db, recipient_id)
                active_run = active_state.get("activeRun")
            active_execution_mode = str((active_run.get("executionMode") if active_run else "") or "").strip().lower()
            recipient_runtime = _normalize_runtime((recipient_row["runtime"] if recipient_row else "") or requested_runtime)
            # ASYMMETRY(hermes): its gateway sidecar does not consume dispatch_controls;
            # route channel/resident steer through its claim loop and native session.steer.
            steer_via_claim = recipient_runtime == "hermes" and active_execution_mode in {"channel", "resident"}
            if steer and active_run and "steer" in capabilities and not steer_via_claim:
                steer_body = f"[Message from {from_agent}]\nSubject: {subject}\n\n{body}"
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=from_agent,
                    action="steer",
                    body=steer_body,
                    source_message_id=source_message_id,
                )
                steer_contract_run_id = None
                if source_message_id:
                    steer_contract_run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                    await db.execute(
                        """
                        INSERT INTO dispatch_runs (
                            id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime,
                            message_type, subject, body, priority, in_reply_to, status, require_reply, requested_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            steer_contract_run_id,
                            source_message_id,
                            from_agent,
                            recipient_id,
                            "steer",
                            execution_mode,
                            requested_runtime or "",
                            message_type,
                            subject,
                            body,
                            priority,
                            in_reply_to,
                            "delivered",
                            1 if require_reply else 0,
                            requested_at,
                        ),
                    )
                    await _append_dispatch_event(
                        db,
                        steer_contract_run_id,
                        "steered",
                        f"Delivered as steer control {control_id} into active run {active_run['runId']}",
                    )
                runs.append({
                    "runId": active_run["runId"],
                    "targetAgentId": recipient_id,
                    "status": "steered",
                    "steered": True,
                    "requireReply": require_reply,
                    "controlId": control_id,
                    "contractRunId": steer_contract_run_id,
                    "steeredIntoActiveRun": {
                        "runId": active_run["runId"],
                        "status": active_run["status"],
                        "subject": active_run["subject"],
                    },
                })
                continue

        # allow_merge=False (channel offline-replay, #238): a merge folds this dispatch
        # into an existing queued run but KEEPS that run's original message_id (see the
        # "Keep message_id … pointing at the FIRST item" comment below), so the replayed
        # message's fanout id would NEVER land on any run — the replay watermark
        # (NOT EXISTS dispatch_runs WHERE message_id = fanout_id) would stay true and the
        # reconciler would re-replay it every 60s sweep, appending the body forever. The
        # replay must therefore insert a DEDICATED run keyed on its own message_id.
        mergeable_run = None
        if allow_merge:
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
                    recipient_status = await _compute_agent_status(recipient_row, db)
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
            # GUARDED merge (review must-fix, 2026-06-10): the run was read as 'queued' but a
            # concurrent /dispatch/claim (BEGIN IMMEDIATE) can flip it to 'claimed' between the
            # read and this write — the bridge then delivers the PRE-merge body and completes the
            # run, silently losing the merged message. Guard on status='queued' and check
            # rowcount: 0 rows updated → the run was claimed mid-merge → fall through to insert a
            # FRESH queued run instead.
            merge_cursor = await db.execute(
                """
                UPDATE dispatch_runs
                SET subject = ?, body = ?, priority = ?, dispatch_mode = ?, message_type = ?, require_reply = ?,
                    queue_if_busy = ?, steer_if_busy = ?
                WHERE id = ? AND status = 'queued'
                """,
                (
                    _build_pending_dispatch_subject(merged_count, subject),
                    merged_body,
                    _stronger_priority(mergeable_run["priority"], priority),
                    "require_start" if mergeable_run["dispatch_mode"] == "require_start" or dispatch_mode == "require_start" else mergeable_run["dispatch_mode"],
                    message_type,
                    1 if (bool(mergeable_run["require_reply"]) or require_reply) else 0,
                    1 if (bool(mergeable_run["queue_if_busy"]) or queue_if_busy) else 0,
                    1 if (bool(mergeable_run["steer_if_busy"]) or steer) else 0,
                    mergeable_run["id"],
                ),
            )
            if merge_cursor.rowcount and merge_cursor.rowcount > 0:
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
            # else: claimed mid-merge — fall through to the fresh-insert path below.

        run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime,
                message_type, subject, body, priority, in_reply_to, status, require_reply,
                queue_if_busy, steer_if_busy, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, source_message_id or None, from_agent, recipient_id, dispatch_mode, execution_mode, requested_runtime or "",
                message_type, subject, body, priority, in_reply_to, "queued", 1 if require_reply else 0,
                1 if queue_if_busy else 0, 1 if steer else 0, requested_at
            )
        )
        await _append_dispatch_event(db, run_id, "queued", f"{message_type}: {subject}")
        runs.append({"runId": run_id, "targetAgentId": recipient_id, "status": "queued", "requireReply": require_reply})
    return runs






def _dispatch_message_id_for_recipient(
    recipient_id: str,
    *,
    message_id: Optional[str],
    source_message_ids: Optional[dict[str, str]] = None,
) -> str:
    return str((source_message_ids or {}).get(recipient_id, message_id or "") or "").strip()


# _dispatch_source_message_ids moved to service/api_core/claim_gating.py in v0.5.4.


# _mark_dispatch_source_messages_read moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_conversation_context moved to service/routers/dispatch_messages/shared.py in v0.5.3.




# _is_replaceable_auto_handoff_message moved to service/routers/dispatch_messages/shared.py in v0.5.3.


# _HANDOFF_REPLY_TYPES moved to service/api_core/reply_contract.py in v0.5.4 with its
# only reader, _message_satisfies_reply_contract (sole-reader move).
# _COMPLETION_INFO_RE moved to service/api_core/reply_contract.py in v0.5.4 with its
# only reader, _message_satisfies_reply_contract (sole-reader move).


# _message_satisfies_reply_contract moved to service/api_core/reply_contract.py in v0.5.4.


# _clear_turn_busy_if_no_open_reply_owing_run moved to service/api_core/turn_state.py in v0.5.4.



_UNTHREADED_HANDOFF_WINDOW_MS = 24 * 60 * 60 * 1000






# _link_unthreaded_completion_message_for_run moved to service/reconcilers/managed_workers.py in v0.5.3.


# _auto_handoff_subject_for_run moved to service/api_core/dispatch_text.py in v0.5.4.


# _is_provider_rate_limit_error moved to service/api_core/dispatch_text.py in v0.5.4.



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
        # Auto-mirrored handoff dispatches for managed claude must also
        # honor insert_messages_via_console=false (channel-route default).
        settings_for_handoff = await _load_settings(db)
        await _apply_channel_routing_to_claude_runs(db, delivery_runs, settings_for_handoff)
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






# _cancel_nonterminal_runs_for_agents moved to service/api_core/agent_removal.py in v0.5.4.



# ─── Root ────────────────────────────────────────────────────────────────────



# ─── Environments ────────────────────────────────────────────────────────────







# ─── Usage / Quota ───────────────────────────────────────────────────────────
# Per-pool subscription quota snapshots POSTed by the env-bridge collector and read
# by the dashboards + comms_usage. In-memory only (single-worker invariant) — see
# service/usage_cache.py and docs/superpowers/specs/2026-06-26-usage-quota-stats-design.md.













# ─── Spawn Requests And Sessions ─────────────────────────────────────────────





















# _default_console_command moved to service/api_core/capabilities.py in v0.5.4.






































# ─── Agents ──────────────────────────────────────────────────────────────────







_CONSOLE_TAIL_MAX_LINES = 200
_CONSOLE_TAIL_MAX_BYTES = 16 * 1024














# Body sentinel prefix on a `stop` terminal control that must ALSO reap the
# MANAGED-HERMES triad (gateway host + delivery loop + daemon), not just the PTY
# (fix/hermes-leak P2). Used by REMOVE: after the agent row is deleted the claim
# can no longer resolve session_mode, so the sentinel carries the triad-reap
# intent forward. The bridge honors runtime=hermes + (sessionMode=managed OR this
# sentinel). The human-readable suffix is preserved for the console.
# _REAP_TRIAD_BODY_SENTINEL moved to service/api_core/agent_terminal_ops.py in v0.5.4 —
# zero carrier readers; its only writer took it.






















# _touch_current_agent_session moved to service/api_core/agent_sessions.py in v0.5.4.




# ─── Messages ────────────────────────────────────────────────────────────────


# ─── Agent Info ──────────────────────────────────────────────────────────────



# _adopt_live_resident_driver moved to service/api_core/agent_sessions.py in v0.5.4.









# _clear_status_state_in_turn moved to service/api_core/turn_state.py in v0.5.4.



















def _wake_agent(agent_id: str):
    """Signal a listening agent that they have new messages."""
    ev = _listen_events.get(agent_id)
    if ev:
        ev.set()


# ─── Dispatch Runs ────────────────────────────────────────────────────────────























# _agent_has_live_claimer moved to service/reconcilers/dispatch_queue.py in v0.5.3.


# _mirror_undeliverable_queued_run_to_sender moved to service/reconcilers/dispatch_queue.py in v0.5.3.




















# _contract_list_query moved to service/api_core/reply_contract.py in v0.5.4.




# _contract_reminder_due moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_reminder_full_every moved to service/api_core/reply_contract.py in v0.5.4.


def _contract_reminder_is_full(reminder_number: int, *, settings: dict[str, Any]) -> bool:
    """Reminder number N (1-based) gets the FULL format when full_every <= 1
    (always full) or N is a multiple of full_every. Everything in between is a
    LIGHT one-liner — reminders never stop firing (no backoff), they just get
    cheaper between the periodic full nudges."""
    full_every = _contract_reminder_full_every(settings)
    if full_every <= 1:
        return True
    if reminder_number <= 0:
        return True  # unknown ordinal — fail safe to the full format
    return reminder_number % full_every == 0


# _contract_reminder_body moved to service/api_core/reply_contract.py in v0.5.4.


async def _run_contract_reminders_once(
    db,
    *,
    request: Optional[Request] = None,
    run_id: Optional[str] = None,
    dry_run: bool = False,
    limit: int = 50,
    now_s: Optional[float] = None,
    recent_only: bool = False,
    target_agent_id: Optional[str] = None,
    ignore_repeat: bool = False,
) -> dict[str, Any]:
    settings = await _load_settings(db)
    where = [
        "AND COALESCE(r.result_message_id, '') = ''",
        "AND r.status NOT IN ('completed','failed','cancelled')",
        "AND r.from_agent != r.target_agent",
        "AND r.target_agent != 'dashboard'",
    ]
    params: list[Any] = []
    if run_id:
        where.append("AND r.id = ?")
        params.append(run_id)
    if target_agent_id:
        where.append("AND r.target_agent = ?")
        params.append(str(target_agent_id).strip())
    if recent_only:
        stale_hours = max(1, int(settings.get("contract_stale_hours", 24) or 24))
        where.append("AND datetime(r.requested_at) >= datetime('now', ?)")
        params.append(f"-{stale_hours} hours")
    params.append(limit)
    cursor = await db.execute(_contract_list_query(where_sql="\n".join(where), order_sql="ORDER BY r.requested_at ASC"), params)
    candidates = await cursor.fetchall()
    reminded = []
    skipped = []
    now_s = now_s or time.time()
    for row in candidates:
        due, reason = _contract_reminder_due(row, settings=settings, now_s=now_s, ignore_repeat=ignore_repeat)
        if not due:
            skipped.append({"runId": row["id"], "reason": reason})
            continue

        terminal_blocked_without_live_backing = False
        if (
            str(row["dispatch_mode"] or "").strip().lower() == "terminal"
            and str(row["status"] or "").strip().lower() in {"claimed", "running"}
        ):
            agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (row["target_agent"],))).fetchone()
            live_state = await _compute_live_status_cache(db, agent_row, settings=settings) if agent_row else {}
            if str(live_state.get("status") or "").strip().lower() == "blocked":
                live_reason = str(live_state.get("reason") or "").strip().lower()
                if live_reason.startswith("awaiting console"):
                    reason = "target is blocked awaiting operator input"
                    skipped.append({"runId": row["id"], "targetAgentId": row["target_agent"], "reason": reason})
                    await _append_dispatch_event(db, row["id"], "reply_reminder_skipped", reason)
                    continue
                if "no live terminal backing" in live_reason:
                    terminal_blocked_without_live_backing = True

        active_state = await _get_dispatch_state_for_agent(db, row["target_agent"])
        # Busy = a claimed/running dispatch run OR a fresh turn_busy (the same
        # definition the status engine + claim-gate use). Without the turn_busy
        # half, a mid-turn agent with no tracked run (resident claude on its own
        # turn) was reminder-nagged while it was clearly working.
        #
        # BUT: a delivered require_reply run sets turn_busy with turn_run_id =
        # THAT run on its own delivery re-pulse. If we treat that as "busy" we
        # skip THIS run's own reminder — forever — and the handoff never gets
        # nudged, so the agent never replies and the run closes stale (confirmed
        # deadlock: ~24 consecutive reply_reminder_skipped "target is busy" then
        # "Closed stale delivered run requiring a reply"). So turn_busy only
        # counts as busy-for-skip when it is for OTHER work — a DIFFERENT run id
        # than the one we are about to remind. A claimed/running dispatch run
        # (hasActiveRun) always counts: the agent is genuinely executing.
        turn_fresh, turn_run_id = await _turn_busy_state(db, row["target_agent"])
        busy_for_other_work = turn_fresh and turn_run_id != row["id"]
        target_busy = bool(active_state.get("hasActiveRun")) or busy_for_other_work
        if target_busy and not terminal_blocked_without_live_backing:
            reason = "target is busy; reminder will be retried when the agent is idle"
            skipped.append({"runId": row["id"], "targetAgentId": row["target_agent"], "reason": reason})
            await _append_dispatch_event(db, row["id"], "reply_reminder_skipped", reason)
            continue

        subject = f"Reminder: reply overdue - {str(row['subject'] or row['id'])[:96]}"
        # The reminder about to be sent is ordinal reminder_count + 1 (the
        # contract query counts prior 'reply_reminder' events for this run).
        prior_reminders = int((row["reminder_count"] if "reminder_count" in row.keys() else 0) or 0)
        body = _contract_reminder_body(
            row,
            full=_contract_reminder_is_full(prior_reminders + 1, settings=settings),
        )
        if dry_run:
            reminded.append({"runId": row["id"], "targetAgentId": row["target_agent"], "subject": subject, "dryRun": True})
            continue

        launchable, not_started = await _preflight_live_send_recipients(
            db,
            [row["target_agent"]],
            allow_steer=True,
            allow_queue_busy=True,
        )
        if not launchable:
            skipped.append({"runId": row["id"], "targetAgentId": row["target_agent"], "reason": "target cannot receive live reminder", "notStarted": not_started})
            await _append_dispatch_event(db, row["id"], "reply_reminder_skipped", json.dumps(not_started))
            continue

        message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        timestamp_ms = int(time.time() * 1000)
        await db.execute(
            """
            INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id,
                row["from_agent"],
                row["target_agent"],
                "direct",
                "info",
                subject,
                body,
                "high" if str(row["priority"] or "").lower() == "urgent" else "normal",
                1,
                row["message_id"] or None,
                timestamp_ms,
            ),
        )
        runs = await _create_dispatch_runs(
            db,
            [target for target, _ in launchable],
            from_agent=row["from_agent"],
            message_type="info",
            subject=subject,
            body=body,
            priority="high" if str(row["priority"] or "").lower() == "urgent" else "normal",
            in_reply_to=row["message_id"] or None,
            dispatch_mode="start_if_possible",
            execution_mode="managed",
            requested_runtime=None,
            message_id=message_id,
            source_message_ids={row["target_agent"]: message_id},
            steer=True,
            require_reply=False,
        )
        finalized = await _finalize_dispatch_runs(db, runs, launchable, not_started)
        await _append_dispatch_event(db, row["id"], "reply_reminder", f"Sent reminder message {message_id}")
        reminded.append({
            "runId": row["id"],
            "targetAgentId": row["target_agent"],
            "messageId": message_id,
            "dispatchRuns": finalized,
        })

    ws = await _get_ws(request) if request else None
    if ws and reminded and not dry_run:
        await ws.broadcast("contract_reminders_sent", {"count": len(reminded)})
    return {"ok": True, "dryRun": dry_run, "reminded": reminded, "skipped": skipped}
























# ─── Shared Artifacts ────────────────────────────────────────────────────────









# ─── Channels ────────────────────────────────────────────────────────────────

















# ─── Settings ────────────────────────────────────────────────────────────────







# ─── Stats ───────────────────────────────────────────────────────────────────









# ─── Clear ───────────────────────────────────────────────────────────────────



# ─── Rotate ──────────────────────────────────────────────────────────────────



# ─── Dashboard compatibility redirects ──────────────────────────────────────







