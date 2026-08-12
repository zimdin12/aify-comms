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

_WINDOWS_DRIVE_CWD_RE = re.compile(r"^[a-zA-Z]:/")
_WSL_DRIVE_CWD_RE = re.compile(r"^/mnt/[a-zA-Z](?:/|$)")

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


def _is_lock_error(exc: BaseException) -> bool:
    """True for a transient SQLite contention error (`database is locked` / `busy`). Used by
    the read endpoints to skip their best-effort cache writes and serve cached data rather than
    503 — a SELECT never takes the write lock in WAL, so a read can always succeed."""
    message = str(exc or "").lower()
    return "locked" in message or "busy" in message






_MANUAL_STATUSES = {"stopped"}

# _TERMINAL_MONOTONIC_STATUSES moved to service/routers/terminals.py in v0.5.3 with its only
# reader, _terminal_status_transition. _TERMINAL_ACTIVE_STATUSES below STAYS: api_v2 still reads it.
# _TERMINAL_ACTIVE_STATUSES moved to service/api_core/terminal_status.py in v0.5.4.
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
_DISPATCH_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_TERMINAL_END_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
# Deterministic, lowercase ordering of the SAME set, for SQL parameter binding. A set
# gives no ordering guarantee across builds and an inline literal list in a query is
# how the two managed-worker sweeps came to disagree about `degraded` (finding N7) —
# `test_terminal_status_sets_agree` fails the suite if these two ever diverge.
_TERMINAL_END_STATUSES_ORDERED = tuple(sorted(s.lower() for s in _TERMINAL_END_STATUSES))
_DISPATCH_ACTIVE_STATUSES = {"queued", "claimed", "running"}
_SESSION_DELETE_ALLOWED_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
# A session whose spawn/run is in flight or live. "starting" is included so a
# spawn-in-progress is not marked offline merely because the environment bridge
# instance id rotated (same rationale as a running session surviving a bridge
# restart); genuine staleness is still caught by env-offline/heartbeat checks.
_LIVE_SESSION_STATUSES = {"starting", "running", "recovering", "restarting", "cli-takeover"}
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
TURN_BUSY_BACKSTOP_SECONDS = 30 * 60
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
_NATIVE_MANAGED_RUNTIMES = {"codex", "pi", "opencode", "hermes"}
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

def _managed_terminal_backing_enabled(settings: dict[str, Any]) -> bool:
    return bool(settings.get("managed_terminal_backing_enabled", DEFAULT_SETTINGS["managed_terminal_backing_enabled"]))


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
CLAUDE_RESIDENT_DELIVERY_SUMMARY_PREFIX = "Delivered to Claude resident session"
CLAUDE_CHANNEL_DELIVERY_SUMMARY_PREFIX = "Delivered to Claude channel session"




# _touch_agent moved to service/api_core/agent_sessions.py in v0.5.4.
















_SHELL_PLACEHOLDER_HANDLE_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")




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








# _machine_family moved to service/routers/agents/shared.py in v0.5.3 — the agents package was its
# only consumer once the domains moved, so the borrow shim became the last thing keeping it here.












def _is_delivery_only_claude_run(row) -> bool:
    if not row:
        return False
    if str((row["runtime"] if "runtime" in row.keys() else "") or "").strip() != "claude-code":
        return False
    if str((row["status"] if "status" in row.keys() else "") or "").strip().lower() != "completed":
        return False
    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    # Both resident and channel bridges write a delivery-receipt summary
    # for runs they handed off to the Claude session. The summary is NOT
    # the agent's actual reply — it's just confirmation the dispatch
    # reached the bridge. Without including the channel prefix here, the
    # mirror function persisted the receipt as a fake "Re: Hello"
    # response with body "Delivered to Claude channel session; awaiting
    # explicit reply" — observed live as the misleading reply operator
    # caught.
    return (
        summary.startswith(CLAUDE_RESIDENT_DELIVERY_SUMMARY_PREFIX)
        or summary.startswith(CLAUDE_CHANNEL_DELIVERY_SUMMARY_PREFIX)
    )


def _dispatch_reply_state(row) -> str:
    if str((row["result_message_id"] if row else "") or "").strip():
        return "sent"
    if not _row_require_reply(row):
        return "not_required"
    if _is_delivery_only_claude_run(row):
        return "awaiting"
    status = str((row["status"] if row else "") or "").strip().lower()
    if status in _DISPATCH_TERMINAL_STATUSES:
        return "pending"
    return "awaiting"


# _dispatch_reply_pending moved to service/routers/dispatch_messages/shared.py in v0.5.3 — the
# dispatch+messages package was its only consumer. `_dispatch_reply_state`, which it calls, is still
# router-owned and stays borrowed there.


# _is_operator_closed_contract moved to service/api_core/reply_contract.py in v0.5.4.


def _contract_reply_expected(row) -> bool:
    if not row:
        return False
    if _is_operator_closed_contract(row):
        return False
    # Send creation has already normalized type defaults plus the explicit requireReply
    # override into this field. Re-inferring from type/priority here made an explicit
    # requireReply=false request actionable again and recreated reminder/reply debt.
    return _row_require_reply(row)


def _contract_state(row, *, settings: dict[str, Any], now_s: Optional[float] = None) -> dict[str, Any]:
    now_s = now_s or time.time()
    requested_s = _iso_to_epoch((row["requested_at"] if row and "requested_at" in row.keys() else "") or "")
    age_minutes = max(0.0, (now_s - requested_s) / 60.0) if requested_s else 0.0
    status = str((row["status"] if row and "status" in row.keys() else "") or "").strip().lower()
    result_message_id = str((row["result_message_id"] if row and "result_message_id" in row.keys() else "") or "").strip()
    reply_expected = _contract_reply_expected(row)
    reminder_minutes = max(1, int(settings.get("reply_reminder_minutes", DEFAULT_SETTINGS["reply_reminder_minutes"]) or DEFAULT_SETTINGS["reply_reminder_minutes"]))
    reminder_count = int((row["reminder_count"] if row and "reminder_count" in row.keys() else 0) or 0)
    source_read_at = str((row["source_read_at"] if row and "source_read_at" in row.keys() else "") or "").strip()
    same_agent = str((row["from_agent"] if row else "") or "") == str((row["target_agent"] if row else "") or "")

    if result_message_id:
        state = "answered"
    elif status in {"failed", "cancelled"}:
        state = "failed"
    elif status == "completed":
        state = "missing_reply" if reply_expected else "closed"
    elif status in {"claimed", "running"}:
        state = "working"
    elif status == "queued":
        state = "queued"
    elif source_read_at:
        state = "seen"
    else:
        state = "sent"

    overdue = bool(
        reply_expected
        and not result_message_id
        and status not in _DISPATCH_TERMINAL_STATUSES
        and age_minutes >= reminder_minutes
    )
    if overdue:
        state = "overdue"

    category = "self_wake" if same_agent else "direct"
    source = str((row["message_source"] if row and "message_source" in row.keys() else "") or "").strip().lower()
    if source == "channel":
        category = "channel"

    return {
        "state": state,
        "replyExpected": reply_expected,
        "overdue": overdue,
        "ageMinutes": round(age_minutes, 1),
        "reminderCount": reminder_count,
        "category": category,
        "actionable": bool(reply_expected and not result_message_id and category != "self_wake"),
    }






def _has_codex_live_app_server(runtime_config: Optional[dict[str, Any]] = None) -> bool:
    if not isinstance(runtime_config, dict):
        return False
    return str(runtime_config.get("appServerUrl") or "").strip().lower().startswith(("ws://", "wss://"))


# _has_hermes_gateway_url moved to service/api_core/capabilities.py in v0.5.4.








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
        # Also clear the reply LINK (bughunt 2026-07-03): if a deleted/unsent message was
        # a run's recorded reply, leaving result_message_id pointing at the now-gone row
        # kept the contract 'answered' with no reply behind it — it never re-opened.
        await db.execute(f"UPDATE dispatch_runs SET result_message_id = NULL WHERE result_message_id IN ({placeholders})", chunk)
        await db.execute(f"UPDATE dispatch_controls SET source_message_id = '' WHERE source_message_id IN ({placeholders})", chunk)
        await db.execute(f"DELETE FROM read_receipts WHERE message_id IN ({placeholders})", chunk)
        cursor = await db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", chunk)
        deleted += cursor.rowcount or 0
    return deleted




async def _delete_messages_where(db, where_clause: str, params: tuple[Any, ...] = ()) -> int:
    message_ids = await _select_message_ids(db, where_clause, params)
    return await _delete_messages_by_ids(db, message_ids)


# _agent_tombstone moved to service/api_core/agent_sessions.py in v0.5.4.


# _tombstone_agent moved to service/api_core/agent_sessions.py in v0.5.4.


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
    # channel_members has no FK on agent_id, so removing an agent left GHOST memberships
    # (bughunt 2026-07-03): they permanently inflate memberCount AND every later channel
    # send INSERTs an undeliverable inbox row for the deleted agent (unbounded per-post
    # growth). Clean them up here.
    await db.execute("DELETE FROM channel_members WHERE agent_id = ?", (agent_id,))
    cursor = await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    # Evict the in-memory derived-status entry too (audit 2026-06-28): SQLite per-agent rows
    # cascade-delete, but _LIVE_STATE_CACHE is a process-global dict and would otherwise keep a
    # stale (never-served) entry forever — small unbounded leak across removed agent ids.
    _live_state_drop(agent_id)
    return cursor.rowcount or 0


# _default_capabilities_for moved to service/api_core/capabilities.py in v0.5.4.




def _row_capabilities(row) -> list[str]:
    if not row:
        return []
    capabilities = _json_loads_or(row["capabilities"], [])
    runtime = _normalize_runtime((row["runtime"] if "runtime" in row.keys() else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if "session_mode" in row.keys() else "") or "resident")
    session_handle = str((row["session_handle"] if "session_handle" in row.keys() else "") or "").strip()
    runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
    if runtime == "pi":
        if session_mode == "resident":
            return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
        if session_mode == "managed":
            for cap in ("managed-run", "resume", "interrupt", "steer", "spawn"):
                if cap not in capabilities:
                    capabilities = [*capabilities, cap]
    if runtime == "opencode" and session_mode == "resident":
        return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
    if runtime == "hermes":
        if session_mode == "managed":
            managed_caps = ["managed-run", "resume", "interrupt", "spawn"]
            if bool(runtime_config.get("channelEnabled")):
                managed_caps.append("steer")
            else:
                capabilities = [cap for cap in capabilities if cap != "steer"]
            for cap in managed_caps:
                if cap not in capabilities:
                    capabilities = [*capabilities, cap]
        elif _has_hermes_gateway_url(runtime_config):
            for cap in ("resident-run", "resume", "interrupt", "steer"):
                if cap not in capabilities:
                    capabilities = [*capabilities, cap]
        else:
            return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
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
    # Plan 4 Task 17: resident hermes uses gateway path (hermes-live) — the
    # bridge captures gatewayUrl via discoverSessionId after hermes-aify starts,
    # so resident hermes wake-mode is always gateway-channel. The legacy
    # hermes-session-resume mode (spawn fresh hermes with provider config) is
    # dead code post Plan 4 Task 7; gateway is the single source.
    if session_mode == "resident" and runtime == "hermes" and "resident-run" in capabilities and _has_hermes_gateway_url(runtime_config):
        return "hermes-live"
    if session_mode == "resident" and runtime in {"opencode", "pi"}:
        return "presence-only"
    if session_mode == "resident" and runtime == "codex" and not session_handle:
        return "codex-missing-handle"
    if session_mode == "resident" and runtime == "hermes" and not _has_hermes_gateway_url(runtime_config):
        return "hermes-missing-handle"
    if session_mode == "resident" and runtime == "opencode" and not session_handle:
        return "opencode-missing-handle"
    if session_mode == "resident" and runtime == "pi" and not session_handle:
        return "pi-missing-handle"
    if session_mode == "resident" and runtime == "claude-code":
        return "claude-needs-channel"
    return "message-only"


def _agent_execution_mode(row, requested_runtime: Optional[str] = None, settings: Optional[dict[str, Any]] = None) -> tuple[Optional[str], Optional[str]]:
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
        # Unified-backing refactor 2026-05-24: when this runtime is
        # wrapper-backed (managed_via_wrapper includes it), route managed
        # dispatches as execution_mode='channel'. The wrapper's child bridge
        # (loaded as MCP inside *-aify, running with sessionMode=resident)
        # claims via its resident-run capability and executionModes=['channel',
        # 'resident'] — same shape as channel-route managed claude. The main
        # bridge no longer claims 'managed' for wrapper-backed runtimes
        # (mcp/stdio/dispatch-execution.js supportedExecutionModes gate).
        if settings is not None and _managed_via_wrapper_for_runtime(settings, runtime):
            return "channel", None
        # Managed claude with channelEnabled=true uses the channel
        # transport, not the headless managed-run API (claude doesn't
        # have a true headless managed-run). The wrapper-PTY-hosted
        # claude-channel.js delivers via channel notifications. Skip
        # the managed-run cap check for that path; the dispatch flows
        # through execution_mode='channel' below.
        runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
        # Sidecar-channel managed delivery (claude unconditional; hermes
        # gated on the wrapper-set channelEnabled flag). The in-session
        # sidecar claims the channel run and delivers the wake; the agent
        # self-replies via comms_send. The channel path needs no captured
        # session_handle — the sidecar drives the agent's own session — so
        # this returns before any handle requirement (Task 1.5: hermes
        # delivery no longer needs session_handle).
        _channel_eligible = _channel_managed_eligible(runtime, runtime_config)
        if capabilities and "managed-run" not in capabilities and not _channel_eligible:
            return None, 'agent capabilities do not include "managed-run"'
        # claude: unconditional channel (no native managed-run). hermes: channel
        # only when the flag is set; otherwise it falls through to its native
        # 'managed' route. ASYMMETRY(hermes): documented at the set definitions.
        if runtime in _CHANNEL_MANAGED_RUNTIMES:
            return "channel", None
        if runtime in _CHANNEL_FLAG_GATED_RUNTIMES and _channel_eligible:
            return "channel", None
        return "managed", None
    if runtime == "pi":
        return None, (
            f'agent "{row["id"]}" is a Pi/OMP presence session, not a triggerable resident target. '
            "Switch to managed or spawn a managed Pi agent so delivery uses the bridge-owned OMP RPC worker."
        )
    if runtime == "opencode":
        return None, (
            f'agent "{row["id"]}" is an OpenCode presence session, not a triggerable resident target. '
            "Create an environment-managed OpenCode agent; resident OpenCode delivery is disabled until a real multi-client surface is wired."
        )
    if "resident-run" not in capabilities:
        # Actionable diagnosis: identify the most likely missing wake-config
        # for this runtime so the operator can fix the registration without
        # spelunking docs. Mirror of mcp/stdio/runtimes.js:defaultCapabilities-
        # ForRuntime gating: bridge returns [] for resident agents missing
        # their runtime-specific wake handle (sessionHandle for codex/pi/
        # opencode; gatewayUrl for hermes; channelEnabled for claude).
        runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
        runtime_config = runtime_config if isinstance(runtime_config, dict) else {}
        if runtime == "claude-code" and not runtime_config.get("channelEnabled"):
            return None, (
                f'agent "{row["id"]}" is a resident Claude session without channelEnabled. '
                "Restart with `claude-aify` (which sets AIFY_CHANNELS_ENABLED=1) and re-register from that session."
            )
        if runtime == "codex" and not _has_codex_live_app_server(runtime_config):
            return None, (
                f'agent "{row["id"]}" is a resident Codex session without a live appServerUrl. '
                "Restart with `codex-aify` and re-register passing `appServerUrl=\"$AIFY_CODEX_APP_SERVER_URL\"` and `sessionHandle=\"$CODEX_THREAD_ID\"`."
            )
        if runtime == "hermes":
            gateway_url = str(runtime_config.get("gatewayUrl") or "").strip()
            if not (gateway_url.startswith("ws://") or gateway_url.startswith("wss://")):
                return None, (
                    f'agent "{row["id"]}" is a resident Hermes session without a live gatewayUrl. '
                    "Restart with the updated `hermes-aify` (which exports AIFY_HERMES_GATEWAY_URL) and re-register — the bridge auto-detects the gateway from env. "
                    "Verify the wrapper is current with `head -30 ~/.local/bin/hermes-aify | grep pick_port` (function exists in the new wrapper)."
                )
        return None, 'agent capabilities do not include "resident-run" — re-register from a live aify-wrapper session with the runtime\'s wake handle.'
    if runtime == "codex" and not session_handle:
        return None, (
            f'agent "{row["id"]}" is a resident Codex session without a bound session handle. '
            "Re-register that live session or provide sessionHandle explicitly."
        )
    if runtime == "hermes" and not session_handle:
        # Hermes-with-gatewayUrl doesn't need a captured sessionHandle —
        # the bridge's gateway-channel controller resolves
        # session.most_recent at dispatch time. Mirror of the carve-out
        # in defaultCapabilitiesForRuntime (mcp/stdio/runtimes.js).
        # Operator-reported 2026-05-24: sc-hermes-test-1 registered with
        # gatewayUrl but no sessionHandle, capability check passed (resident-run
        # was granted) but this gate still rejected live delivery. Without this
        # carve-out the new gateway path can never deliver since hermes-aify
        # registers before any chat session exists.
        _rc = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
        _rc = _rc if isinstance(_rc, dict) else {}
        _gw = str(_rc.get("gatewayUrl") or "").strip()
        if not (_gw.startswith("ws://") or _gw.startswith("wss://")):
            return None, (
                f'agent "{row["id"]}" is a resident Hermes session without a bound session handle. '
                "Restart with hermes-aify and a resumable session handle, or create an environment-managed session."
            )
    if (row["launch_mode"] or "detached") == "none":
        return None, "launch mode is disabled"
    return "resident", None


async def _managed_environment_unavailable_reason(db, row) -> Optional[str]:
    environment_id, env_status, _env_bridge = await _managed_environment_status(db, row)
    if not environment_id:
        return None
    if env_status not in {"online", "degraded"}:
        return f'managed environment "{environment_id}" is {env_status}'
    return None


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

    if session_mode == "managed" and (row["launch_mode"] or "detached") == "none":
        hint["fix"] = "Enable launch mode or recreate this agent as an environment-managed session."
        hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
        return hint

    hint["fix"] = "Inspect the target runtime/session with comms_agent_info, then retry with runtime-specific steps."
    hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
    return hint


# _format_dispatch_state moved to service/api_core/dispatch_text.py in v0.5.4.


# _get_dispatch_state_for_agent moved to service/api_core/dispatch_state.py in v0.5.4.


# _get_dispatch_state_map moved to service/api_core/dispatch_state.py in v0.5.4.


async def _get_unread_count_map(db, agent_ids: list[str]) -> dict[str, int]:
    if not agent_ids:
        return {}
    placeholders = ",".join("?" for _ in agent_ids)
    cursor = await db.execute(
        f"""
        SELECT m.to_agent AS agent_id, COUNT(*) AS unread_count
        FROM messages m
        LEFT JOIN read_receipts rr ON m.id = rr.message_id AND rr.agent_id = m.to_agent
        WHERE m.to_agent IN ({placeholders}) AND rr.message_id IS NULL
        GROUP BY m.to_agent
        """,
        tuple(agent_ids),
    )
    rows = await cursor.fetchall()
    return {row["agent_id"]: int(row["unread_count"] or 0) for row in rows}



async def _get_blocking_active_run(db, agent_id: str, exclude_run_id: str = "") -> Optional[dict[str, Any]]:
    state = await _get_dispatch_state_for_agent(db, agent_id)
    active = state.get("activeRun")
    if not active:
        return None
    if exclude_run_id and active.get("runId") == exclude_run_id:
        return None
    return active


# _resident_bridge_is_fresh moved to service/api_core/liveness.py in v0.5.4.


# _agent_has_live_terminal moved to service/api_core/liveness.py in v0.5.4.


# TODO consolidate existing *_is_fresh helpers (_resident_bridge_is_fresh,
# _owner_bridge_is_fresh, _agent_has_fresh_bridge, _has_live_channel_sidecar,
# _has_live_managed_wrapper_child, _has_live_terminal_session) into
# _agent_liveness — deferred this pass (their many callers make ripping them out a
# separate, risky migration). For now _agent_liveness is the SINGLE predicate the
# new session deriver uses; the legacy helpers stay for their existing callers.
async def _agent_liveness(db, agent_id: str, *, agent_row=None) -> dict[str, bool]:
    """ONE liveness predicate computed from terminal_sessions + bridge_instances.

    Returns {worker_live, console_live, resident_bridge_fresh, sidecar_live}
    using the EXACT lease/window the existing helpers use (resident_lease_seconds
    default 150; the channel-sidecar stale window). Used by
    _compute_session_display_status so the derived session badge reads the same
    live truth as the agent dot.
    """
    agent_id = str(agent_id or "").strip()
    out = {
        "worker_live": False,
        "console_live": False,
        "resident_bridge_fresh": False,
        "sidecar_live": False,
    }
    if db is None or not agent_id:
        return out
    if agent_row is None:
        try:
            agent_row = await (
                await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
            ).fetchone()
        except Exception:
            agent_row = None
    # console/worker truth: a live, non-synth terminal_sessions row.
    out["console_live"] = await _agent_has_live_terminal(db, agent_id)
    out["worker_live"] = out["console_live"]
    # channel-sidecar deliverability (claude-channel.js / hermes delivery loop).
    out["sidecar_live"] = await _has_live_channel_sidecar(db, agent_id)
    # resident bridge freshness — only meaningful for a resident agent. Reuse the
    # exact helper + lease the status engine uses.
    if agent_row is not None:
        try:
            settings = await _load_settings(db)
            lease = int(settings.get("resident_lease_seconds", 150) or 150)
        except Exception:
            lease = 150
        try:
            out["resident_bridge_fresh"] = await _resident_bridge_is_fresh(
                db, agent_row, lease_seconds=lease
            )
        except Exception:
            out["resident_bridge_fresh"] = False
    return out






# _turn_busy_state moved to service/api_core/turn_state.py in v0.5.4.


# _turn_busy_holds_delivery moved to service/routers/dispatch_messages/shared.py in v0.5.3.




# _session_handle_live_owner moved to service/api_core/agent_sessions.py in v0.5.4.




async def _auto_return_resident_to_managed_if_possible(
    db,
    row,
    *,
    settings: dict[str, Any],
    force: bool = False,
    reason: str = "resident_lease_expired",
):
    # Manual ownership model: resident<->managed changes happen only through
    # PATCH /agents/{id}/session-mode. Keep the helper as a compatibility
    # no-op for older call sites while the automatic paths are removed.
    return row, ""




# _bridge_is_superseded moved to service/api_core/liveness.py in v0.5.4.


async def _active_wrapper_terminal_id(db, agent_id: str, *, settings: dict[str, Any]) -> str:
    terminal = await _active_terminal_for_agent(db, agent_id, settings=settings)
    if not terminal:
        return ""
    try:
        return str(terminal["terminal_id"] or terminal["id"] or "").strip()
    except Exception:
        return str((terminal.get("terminal_id") or terminal.get("id") or "") if isinstance(terminal, dict) else "").strip()


# _ANSI_RE was declared HERE as well until v0.5.3, with a NARROWER pattern that did not strip
# DCS/APC/PM/SOS strings. It was dead: the declaration further down rebinds the name before
# any of these functions can run, so every caller — including _terminal_text_compact just
# below — already used the broader one. Removed because it read like the governing
# definition for the function beneath it and was not.


# _terminal_text_compact moved to service/api_core/terminal_text.py in v0.5.4.


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


# _bridge_claim_block_reason moved to service/routers/dispatch_messages/shared.py in v0.5.3.






STUCK_STOPPING_GRACE_SECONDS = 900  # a 'stopping' PTY that never reached 'stopped' is wedged








# _record_channel_sidecar_heartbeat moved to service/api_core/recovery_writes.py in v0.5.4.




# _stop_virtual_terminals_for_superseded_bridges moved to service/routers/agents/shared.py in v0.5.3.


# _fail_active_runs_for_superseded_bridges moved to service/routers/agents/shared.py in v0.5.3.


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


async def _current_active_run_row(db, agent_id: str):
    # Only a genuinely claimed/running dispatch run counts as "working".
    # NOTE: terminal-delivery runs sit 'delivered'+unfinished as their
    # normal lingering state long after the agent finished (they reconcile
    # lazily), so 'delivered' is NOT a reliable working signal — treating it
    # as one pins idle agents to "working" (worse failure mode). Accurate
    # mid-turn detection needs a bridge-reported turn-busy signal, tracked
    # separately; do not re-add a delivered-run heuristic here.
    cursor = await db.execute(
        """
        SELECT id, status, subject, from_agent, dispatch_mode, execution_mode, runtime, requested_at, claimed_at, started_at, claim_bridge_id
        FROM dispatch_runs
        WHERE target_agent = ? AND status IN ('claimed', 'running')
        ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
        LIMIT 1
        """,
        (agent_id,),
    )
    return await cursor.fetchone()


async def _current_channel_awaiting_reply_run_row(db, agent_id: str):
    # claude-channel.js delivers both 'channel' and 'resident' execution_mode
    # dispatches and now (post-fix) marks any require_reply=1 run as
    # 'delivered' to preserve the reply contract. While in 'delivered'
    # awaiting the agent's reply, the agent IS working — surface that as
    # "working" in the dashboard. _current_active_run_row deliberately
    # excludes 'delivered' to avoid pinning idle terminal-delivery agents
    # to working. The discriminator that lets us treat THIS case safely
    # is execution_mode IN ('channel', 'resident') — terminal-delivery
    # runs carry execution_mode='managed', so they're filtered out.
    cursor = await db.execute(
        """
        SELECT id, subject, from_agent, execution_mode, runtime, requested_at, claimed_at, started_at
        FROM dispatch_runs
        WHERE target_agent = ?
          AND status = 'delivered'
          AND execution_mode IN ('channel', 'resident')
          AND require_reply = 1
        ORDER BY COALESCE(started_at, claimed_at, requested_at) DESC
        LIMIT 1
        """,
        (agent_id,),
    )
    return await cursor.fetchone()


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


async def _has_live_worker_for(db, agent_row, *, settings=None) -> bool:
    """True when the agent has a LIVE serving worker (managed: console+sidecar /
    channel-sidecar / wrapper-child; resident: live tracked session/terminal).

    Thin boolean wrapper over _worker_liveness_for — the shared definition used by
    BOTH the legacy _compute_live_status_cache derivation and the event-engine
    _gather_status_inputs, so old/new never disagree on worker liveness. Resolves
    `live_session` from the agent's current session row exactly as the legacy path
    does (a live agent_sessions.status), so the result matches for a given DB state.
    """
    agent_session_mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    session_row = await _current_agent_session_row(db, agent_row["id"])
    session_status = str((session_row["status"] if session_row else "") or "").strip().lower()
    live_session = session_status in _LIVE_SESSION_STATUSES
    result = await _worker_liveness_for(
        db, agent_row, agent_session_mode=agent_session_mode, live_session=live_session
    )
    return result.has_live_worker


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
    if managed_env_bridge_offline:
        # FIX B: owning env bridge is down — hard offline takes precedence over the
        # active-run/terminal derivations below (only the env bridge can host the
        # worker, so any surviving run is moot).
        effective_status = "offline"
        reason = (
            f'Owning environment "{environment_id}" is {env_status or "offline"}; '
            "only its bridge can host this managed worker."
        )
    elif active_run_terminal_missing:
        effective_status = "blocked"
        reason = f'Managed terminal-backed active run has no live terminal backing. Active run: {active_run["subject"] or active_run["id"]}.'
    elif (
        environment_id
        and env_status
        and env_status not in {"online", "degraded"}
        and not (agent_session_mode == "resident" and not resident_bridge_stale)
    ):
        effective_status = "offline"
        reason = f'Environment "{environment_id}" is {env_status}.'
    elif (
        agent_session_mode != "managed"
        and session_bridge_id
        and env_bridge_id
        and session_bridge_id != env_bridge_id
        and not live_session
        and not active_run
    ):
        # STATUS POLICY (2026-06-04): a MANAGED agent is `offline` ONLY when it is
        # disabled/stopped OR its owning environment is unreachable (both handled
        # above: managed_env_bridge_offline + the env-unreachable branches). An
        # orphaned session row whose owning bridge != the current env bridge just
        # means the previous WORKER died — with a reachable env the agent is still
        # lazy-autostartable, so it must rest at `available` (the base derivation at
        # ~L4041), NOT be demoted to offline here. Excluding managed keeps this
        # branch for resident-style sessions, whose liveness is their own bridge.
        effective_status = "offline"
        reason = "Current environment bridge no longer owns the active session."
    elif resident_bridge_stale and not active_run:
        # An expired resident bridge means a DEAD worker → `offline` (the proof-based
        # rewrite dropped the resident-only `stale` label; a lapsed bridge lease IS
        # offline), even when the agent owes a channel reply. (Previously `and not
        # channel_pending_reply_run`
        # suppressed this so the channel-pending branch could manufacture `online`
        # for a dead agent — the FIX-3 bug. The channel-pending branch now refuses
        # to upgrade a dead worker, so this stale derivation is the correct landing.)
        #
        # pure-event-status change #2 (2026-06-02): liveness wins over turn_busy.
        # The `and not turn_busy` guard was REMOVED here. With STATUS now pure-event
        # (the short status window is gone — change #3), a DEAD resident stuck with a
        # lingering turn_busy=1 (a missed turn-end on a now-dead worker) would have
        # SKIPPED this stale branch and fallen into `elif turn_busy → working`, i.e.
        # working-forever. The resident bridge lease (150s, _resident_bridge_is_fresh)
        # is the liveness signal: an expired bridge is a dead worker regardless of any
        # turn_busy=1, so it must derive offline BEFORE the turn_busy branch is reached.
        effective_status = "offline"
        reason = "Resident bridge heartbeat is gone; restart the resident wrapper or switch to managed."
    # A console terminal reaching an end state returns ownership to managed (the
    # runtime contract reverts owner_mode to managed on stop/fail). So it is a
    # fallback-to-managed candidate, not final unavailability: fall through to
    # active-run / heartbeat-freshness, which is the real source of truth.
    elif active_run and terminal_input_hint:
        effective_status = "blocked"
        reason = f'{terminal_input_hint} Active run: {active_run["subject"] or active_run["id"]}.'
    elif (
        agent_session_mode == "managed"
        and has_live_worker
        and terminal_input_hint
        and terminal_status in _TERMINAL_ACTIVE_STATUSES
    ):
        effective_status = "blocked"
        reason = terminal_input_hint
    elif active_run:
        effective_status = "working"
        reason = f'Active run: {active_run["subject"] or active_run["id"]}.'
    elif turn_busy:
        effective_status = "working"
        reason = f"Executing turn ({turn_runtime})." if turn_runtime else "Executing turn."
    elif channel_pending_reply_run:
        # Status-split (2026-05-31): reaching this branch means NOT active_run
        # and NOT turn_busy — the turn ENDED, the agent is IDLE but owes a reply.
        # That is NOT "working" (actively computing) — showing orange working for
        # an idle agent was the operator-reported "blink when not working". It is
        # `online` with an `awaitingReply` flag (the reminder loop nudges it; the
        # Work Loop tracks the open contract). `working` is reserved for a fresh
        # turn_busy or a claimed/running run. NOTE: the runtime's own turn-end
        # signal (claude Stop hook / hermes post_llm_call / codex turn/completed /
        # pi agent_end) clears turn_busy precisely; this branch is the
        # idle-owes-reply state after that.
        # FIX (2026-06-01): only show `online` when the worker is actually live.
        # A DEAD worker that owes a reply must NOT be manufactured into `online`
        # (visible-TUI truthfulness): a managed claude with a dead console/sidecar
        # has has_live_worker=False (status-F1), and a resident with a stale bridge
        # is positively dead. In either case fall through so the
        # available/stale/offline derivation below stands. A live resident with no
        # tracked terminal row (resident_bridge_stale=False, has_live_worker may be
        # False) is NOT dead and keeps the online-awaiting-reply state.
        worker_is_dead = (
            (agent_session_mode == "managed" and not has_live_worker)
            or resident_bridge_stale
        )
        if not worker_is_dead:
            awaiting_reply = True
            if effective_status not in {"offline", "blocked"}:
                effective_status = "online"
            reason = (
                f'Idle — awaiting reply: '
                f'{channel_pending_reply_run["subject"] or channel_pending_reply_run["id"]}.'
            )
    elif session_status in {"recovering", "restarting"} or terminal_status == "stopping":
        effective_status = "working"
        reason = session_status or terminal_status or "Session is transitioning."
    # NOTE: "working" deliberately requires a tracked active run/turn (or a
    # genuine recover/restart transition) — NOT console attachment or console
    # byte activity. Long-lived managed consoles emit ambient output (prompt
    # redraws, keepalives) while the agent is idle; treating that as "working"
    # made idle agents show working forever. An attached-but-runless console
    # is reachable, so it falls through to the heartbeat branch as "active",
    # never "working". (Supersedes the B1 / console-activity heuristics.)
    else:
        # Proof-based rewrite (2026-06-18): the time-decay staleness block that lived
        # here (idle_minutes→`idle`, offline_minutes→`offline`) was REMOVED. It only ever
        # set `effective_status`, which is a byproduct overridden by derive() — and derive()
        # (the authority) does NOT demote a live-but-quiet agent by wall-clock minutes:
        # `offline` comes from worker/bridge liveness, and `idle` no longer exists. Heartbeat
        # liveness is enforced by `refresh_after` (agent_liveness_seconds) + has_live_worker,
        # not a minute threshold here.
        # Task 1.6: surface WHY a channel-enabled managed agent is only
        # `available` rather than deliverable — the channel sidecar
        # (hermes-channel.js) is not heartbeating. Only annotate when we
        # haven't already attached a more specific reason (e.g. offline).
        if effective_status == "available" and channel_managed_no_console and not reason:
            reason = "Worker has no visible console (headless orphan being reaped)."
        elif effective_status == "available" and channel_managed_no_sidecar:
            # BOOT vs DEAF (2026-06-05, operator-chosen): a live console whose sidecar hasn't
            # registered SINCE THE CONSOLE STARTED is BOOTING → DISPLAY `online` so the operator
            # doesn't miss the terminal. A console whose sidecar registered then died stays
            # `available` (not deliverable; 13c4ae8). DISPLAY-ONLY — has_live_worker is unchanged,
            # so a send during boot still QUEUES until the sidecar claims (routing untouched).
            # (Legacy-path display; live engine is `old`. A `status_engine=new` flip would need
            # the same signal in StatusInputs for parity.)
            if await _managed_console_is_booting(db, agent_row["id"]):
                effective_status = "online"
                if not reason:
                    reason = "Console booting (worker starting; deliverable once it claims)."
            elif not reason:
                reason = "No live channel sidecar heartbeat (not deliverable)."
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










async def _drain_and_flip_pi_resident_agents() -> None:
    """Pi delivery flip (Plan 2, 2026-05-25).

    Every ~5s the periodic loop calls this helper. For each pi agent
    marked with runtime_state.pi_resident_pending_flip == True it checks
    that no active or queued dispatch run is currently targeting the
    agent. When clear, the agent migrates from sessionMode=resident to
    sessionMode=managed: session_handle is preserved, capabilities are
    recomputed via _default_capabilities_for (PiAdapter no longer
    supports_resident), the pending-flip flag is cleared, and a
    flipped_at timestamp is recorded.
    """
    db = await get_db()
    try:
        now_iso = _now()
        cursor = await db.execute(
            """
            SELECT id, session_handle, runtime_state, runtime_config
            FROM agents
            WHERE runtime = 'pi'
              AND session_mode = 'resident'
            """
        )
        rows = await cursor.fetchall()
        if not rows:
            return

        for row in rows:
            runtime_state = _json_loads_or(row["runtime_state"], {})
            # Plan 2 backfill: any pi-resident agent is flip-eligible by
            # definition (PiAdapter no longer supports_resident). The
            # pi_resident_pending_flip marker stays useful as a
            # "newly-detected" signal but is not the only gate — agents
            # registered before the Task 16 marker rolled out would
            # otherwise never flip without manual re-registration.

            # Block the flip while any open run is targeting the agent.
            run_cursor = await db.execute(
                """
                SELECT COUNT(*) AS cnt FROM dispatch_runs
                WHERE target_agent = ?
                  AND status IN ('queued', 'claimed', 'running')
                """,
                (row["id"],),
            )
            run_row = await run_cursor.fetchone()
            if run_row and int(run_row["cnt"] or 0) > 0:
                continue  # wait until next tick

            runtime_state["pi_resident_pending_flip"] = False
            runtime_state["flipped_at"] = now_iso

            runtime_config = _json_loads_or(row["runtime_config"], {})
            new_caps = _default_capabilities_for(
                "pi",
                "managed",
                str(row["session_handle"] or ""),
                runtime_config,
            )

            await db.execute(
                """
                UPDATE agents
                SET session_mode = 'managed',
                    runtime_state = ?,
                    capabilities = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(runtime_state),
                    json.dumps(new_caps),
                    now_iso,
                    row["id"],
                ),
            )
        await db.commit()
    finally:
        await db.close()


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


async def _has_claimable_spawn_request(db, agent_id: str) -> bool:
    """True when a queued/claimed spawn_request already backs this agent.

    A claimable spawn_request means a bridge will (or already did) spawn the
    worker, so the dispatch can safely sit queued instead of being rejected.
    """
    row = await (await db.execute(
        "SELECT id FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed') LIMIT 1",
        (agent_id,),
    )).fetchone()
    return bool(row)


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


def _coldstart_refusal(warnings: Optional[list[str]], reason: str) -> bool:
    """Record WHY cold-start refused, then return False (the caller's expected falsey)."""
    if warnings is not None:
        warnings.append(f"{COLDSTART_REFUSED_PREFIX}{reason}")
    return False

# _coldstart_refusal_message moved to service/api_core/dispatch_text.py in v0.5.4.



async def _coldstart_spawn_request_for_dispatch(
    db,
    agent_id: str,
    *,
    runtime: str,
    settings: dict[str, Any],
    requested_by: str,
    warnings: Optional[list[str]] = None,
) -> bool:
    """Cold-start a managed worker on the send path.

    When a managed agent has no live agent_sessions row, _ensure_managed_pty_for_dispatch
    cannot build a PTY (it has nothing to launch into) and returns None — the dispatch
    then sits queued with nothing that will ever claim it (root cause G). This creates a
    spawn_request through the SAME mechanism as create_spawn_request so a bridge claims it,
    registers a session, and the PATCH->running eager-spawn brings up the wrapper PTY.

    Idempotent: returns False (creating nothing) when a claimable spawn_request
    (queued/claimed) already exists for the agent, or when no environment/runtime can be
    resolved. Returns True when a new spawn_request was inserted.

    G1 (2026-06-03): the inserted spawn_request now carries the agent's current
    native session_handle so the managed worker RESUMES the existing native session
    rather than starting fresh (resident->managed no longer loses the thread).

    G3 (2026-06-03): when `warnings` is provided and the bound handle is already
    owned by a DIFFERENT live agent, an advisory (non-blocking) warning string is
    appended for the caller to surface.
    """
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        return _coldstart_refusal(
            warnings, f"runtime {normalized_runtime or '(unset)'!r} is not cold-startable")

    # Resident-safety (2026-07-06): NEVER auto-cold-start a MANAGED worker for a
    # session_mode='resident' agent — regardless of whether its resident bridge is
    # fresh or disconnected. resident<->managed is OPERATOR-ONLY (manual ownership
    # model); an automatic switch is wrong in both states:
    #   * resident LIVE  → its own claude-channel.js sidecar claims the channel
    #     dispatch; a managed worker would be a DUPLICATE beside the resident
    #     (operator-reported "2 aicm-lc-managers": lc's reply spawned a managed twin).
    #   * resident DISCONNECTED → auto-switching to managed SPLITS delivery: the
    #     resident (on reconnect) still sends, but replies land on the managed twin,
    #     so "none come back to the actual agent" (operator-rejected). The dispatch
    #     should queue for the resident and deliver when it reconnects, not fork a
    #     managed identity behind the operator's back.
    # A deliberate Switch-to-managed flips session_mode to 'managed' BEFORE cold-start,
    # so this never blocks an intentional resident->managed transition.
    _agent_row = await (await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if _agent_row is not None and str(_agent_row["session_mode"] or "").strip().lower() == "resident":
        return _coldstart_refusal(
            warnings,
            "this agent is RESIDENT — auto-cold-start is refused so a managed twin cannot be "
            "forked beside a live resident session. Switch it to managed first if that is intended")

    # Don't pile up duplicate cold-starts — a queued/claimed/recently-running spawn_request
    # is already a (possibly mid-boot) backing for this agent. Bug D fix (2026-07-02): the
    # live repro created a duplicate 41s after the first while the worker was still booting,
    # and the duplicate's kill-prior can murder the booting worker.
    existing = await _has_pending_or_booting_spawn_request(db, agent_id)
    if existing:
        return _coldstart_refusal(
            warnings,
            "a spawn for this agent is ALREADY IN FLIGHT (queued/claimed/starting/running) — "
            "waiting for it rather than starting a second one that could kill the booting worker. "
            "If it never becomes live, that spawn request is the thing to inspect")

    # Resolve environment/runtime/workspace from the agent's most-recent session
    # (any status). A previously-managed agent always leaves one behind.
    session = await (await db.execute(
        """
        SELECT *
        FROM agent_sessions
        WHERE agent_id = ?
        ORDER BY last_seen DESC
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()

    offline_seconds = settings.get("environment_offline_seconds", 90)
    environment = None
    fallback_workspace = ""
    prior_spec = None

    # Prefer the agent's prior-session environment when it is still ONLINE and
    # still advertises the runtime — preserves workspace + spawn_spec continuity.
    if session and str(session["environment_id"] or "").strip():
        env_row = await (await db.execute(
            "SELECT * FROM environments WHERE id = ?", (str(session["environment_id"]).strip(),)
        )).fetchone()
        if env_row:
            candidate = _environment_record_to_dict(env_row, offline_seconds=offline_seconds)
            if (
                str(candidate.get("status") or "").lower() == "online"
                and _runtime_capability_for_environment(candidate, normalized_runtime)
            ):
                environment = candidate
                fallback_workspace = session["workspace"] or ""
                prior_spec_id = str(session["spawn_spec_id"] or "").strip()
                if prior_spec_id:
                    prior_spec = await (await db.execute(
                        "SELECT * FROM spawn_specs WHERE id = ?", (prior_spec_id,)
                    )).fetchone()

    # Phase 2 auto-bind: a managed agent with NO usable session env (never run,
    # or no env binding at all) gets bound to the freshest ONLINE environment
    # that advertises the runtime so an `available` agent can be woken on first
    # message — mirroring comms_spawn's env-omission auto-select. Without this
    # the send path rejects with "cannot start live work now" (operator-reported
    # sc-coder bug). No online env supports the runtime → decline (caller
    # surfaces a clear "no environment available" rejection).
    #
    # NOTE: an agent whose SPECIFIC bound env is merely offline does NOT reach
    # this fallback via the send path — preflight (_managed_environment_unavailable_reason)
    # rejects it first, and that is deliberate: a managed agent's workspace lives
    # on its bound env's machine, so it should wait for that env to return rather
    # than silently migrating to a different machine where its workspace may not
    # exist. This fallback only fires when there is no usable bound env to wait for.
    if environment is None:
        environment = await _select_online_environment_for_runtime(
            db, normalized_runtime, offline_seconds=offline_seconds
        )
        if environment is None:
            return _coldstart_refusal(
                warnings, f"the environment bound to this agent could not be resolved")

    environment_id = str(environment.get("id") or "").strip()
    if not environment_id:
        return _coldstart_refusal(warnings, "the resolved environment has no id (corrupt row)")

    workspace, workspace_root = _workspace_for_environment(environment, None, fallback_workspace)

    # G1 (2026-06-03): carry the agent's CURRENT native session handle into the
    # cold-start spawn_request so the managed worker RESUMES the existing native
    # session (codex thread / hermes gateway session / claude transcript) instead
    # of starting a fresh one. Without this the resident->managed switch (which
    # cold-starts via this helper) silently loses the live native session — the
    # dedicated restart path already carries session["session_handle"] for the
    # same reason. Sourced from the agents row (the durable pinned handle), with a
    # fallback to the prior session row's handle when the agent row has none.
    agent_row = await (await db.execute(
        "SELECT session_handle FROM agents WHERE id = ?", (agent_id,)
    )).fetchone()
    coldstart_session_handle = str(
        (agent_row["session_handle"] if agent_row else "")
        or (session["session_handle"] if session else "")
        or ""
    ).strip()

    # G3 (2026-06-03): warn (do NOT block) when the handle we're about to bind is
    # already owned by a DIFFERENT live agent — two live agents must not share one
    # native session id (e.g. lc-coder + lc-tech-lead on one codex thread). The
    # caller surfaces this via the returned warning; cold-start still proceeds.
    if coldstart_session_handle:
        _settings_g3 = settings if isinstance(settings, dict) else await _load_settings(db)
        _owner_g3 = await _session_handle_live_owner(
            db, coldstart_session_handle, exclude_agent_id=agent_id,
            lease_seconds=_settings_g3.get("resident_lease_seconds", 150),
        )
        if _owner_g3 and warnings is not None:
            warnings.append(
                f"session id '{coldstart_session_handle}' is already owned by live agent "
                f"'{_owner_g3['agentId']}' ({_owner_g3['sessionMode']}); two live agents "
                "should not share one native session."
            )

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
            agent_id,
            environment_id,
            normalized_runtime,
            workspace,
            str(prior_spec["model"] or "") if prior_spec else "",
            str(prior_spec["profile"] or "") if prior_spec else "",
            "managed-warm",
            str(prior_spec["system_prompt"] or "") if prior_spec else "",
            str(prior_spec["standing_instructions"] or "") if prior_spec else "",
            str(prior_spec["env_vars"] or "{}") if prior_spec else "{}",
            str(prior_spec["channel_ids"] or "[]") if prior_spec else "[]",
            str(prior_spec["budget_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["context_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["restart_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["metadata"] or "{}") if prior_spec else "{}",
            now,
            now,
        ),
    )
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
            requested_by or "dispatch-coldstart",
            environment_id,
            agent_id,
            "coder",
            agent_id,
            normalized_runtime,
            workspace,
            workspace_root,
            "",
            "normal",
            f"Cold-start for {agent_id}",
            "managed-warm",
            "native_first",
            "queued",
            coldstart_session_handle,
            now,
            now,
        ),
    )
    return True


async def _ensure_managed_pty_for_dispatch(
    db, agent_id: str, *, runtime: str, settings: dict[str, Any], requested_by: str,
    for_session_id: str = "",
):
    """`for_session_id` scopes adoption to ONE session, and a restart is why it exists.

    REPRODUCED LIVE 2026-08-11 (restarttest-claude, first attempt, deterministic). A managed
    restart creates a new spawn and a new session, and the new spawn reaches `running` about two
    seconds BEFORE the old worker's terminal is torn down. `_active_terminal_for_agent` picks the
    agent's most-recently-seen session that has a terminal — which at that instant is still the OLD
    one — so this function said "there is already a PTY" and created nothing. The restart then
    killed that terminal, leaving:

        spawn_requests.status = 'running'   with NO terminal at all, ever
        agent status           = available
        the operator            looking at a session that says stopped

    `ef-manager` sat in exactly that state today after `graph-tech-lead` restarted it, and it took a
    cold-start send to recover. The v0.2.0 dead-terminal finalizer cannot clean it up either,
    because that keys on a terminal being DEAD and here none was ever created.

    Adoption across dispatches WITHIN a session is the whole point of this function and is
    unchanged. What is no longer allowed is adopting a terminal belonging to a DIFFERENT session —
    at a restart that terminal is, by definition, the one being destroyed.
    """
    wanted_session = str(for_session_id or "").strip()
    active = await _active_terminal_for_agent(db, agent_id, settings=settings)
    # `active` is a sqlite3.Row, NOT a dict — it has no `.get()`. The first version of this line
    # called `active.get("session_id")`, which raises AttributeError, and the caller's
    # `except Exception: pass` swallowed it whole. Worse than a plain crash: it only triggered when
    # an active terminal EXISTED, i.e. exactly the restart case this function was being fixed for,
    # and only when the outgoing terminal had not yet flipped to `stopped` — so the first live test
    # passed by luck and the second hung with no worker and no log line.
    if active and (not wanted_session or str(active["session_id"] or "") == wanted_session):
        return active
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        return None

    if wanted_session:
        # Use the caller's session outright. Re-deriving it by `last_seen` would land on the
        # outgoing session for the same two seconds that caused the bug above.
        session = await (await db.execute(
            "SELECT * FROM agent_sessions WHERE id = ? AND agent_id = ?", (wanted_session, agent_id)
        )).fetchone()
    else:
        session = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
              AND runtime = ?
              AND status IN ('running', 'recovering')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, normalized_runtime),
        )).fetchone()
    if not session:
        return None
    if normalized_runtime == "pi" and not str(session["session_handle"] or "").strip():
        return None

    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (session["environment_id"],))).fetchone()
    if not env_row:
        return None
    environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
    if str(environment.get("status") or "").lower() != "online":
        return None
    if not _environment_supports_terminal(environment, session["runtime"]):
        return None

    workspace, _workspace_root = _workspace_for_environment(environment, None, session["workspace"] or "")
    terminal_id = f"term_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    bridge_id = str(environment.get("bridgeId") or "").strip()
    command = _default_console_command(session, workspace)
    now = _now()
    await db.execute(
        """
        INSERT INTO terminal_sessions (
            id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
            output, status, requested_by, created_at, updated_at, stopped_at, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            terminal_id,
            session["id"],
            agent_id,
            session["environment_id"],
            bridge_id,
            session["runtime"],
            workspace,
            command,
            "",
            "starting",
            requested_by or "dashboard",
            now,
            now,
            None,
            "",
        ),
    )
    await _append_terminal_event(
        db,
        terminal_id,
        "managed_pty_start_requested",
        json.dumps({"requestedBy": requested_by or "dashboard", "sessionId": session["id"], "workspace": workspace, "command": command}),
    )
    await _append_terminal_control(
        db,
        terminal_id=terminal_id,
        environment_id=session["environment_id"],
        bridge_id=bridge_id,
        action="start",
        requested_by=requested_by or "dashboard",
        body=command,
    )
    # Publish the wrapper PTY's terminal_session id into agent.runtime_state.terminalId
    # so the dashboard's chooseSessionConsoleWidget (service/new_dashboard/app.js)
    # can render xterm against it. Without this the row is orphaned from the
    # runtime_state-driven rendering — only ensure_virtual_terminal publishes
    # virtualTerminalId (native RPC adapter path). Operator-reported 2026-05-24:
    # wrapper PTY existed but dashboard couldn't see it.
    agent_runtime_state_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if agent_runtime_state_row:
        _agent_rs = _json_loads_or(agent_runtime_state_row["runtime_state"], {})
        if not isinstance(_agent_rs, dict):
            _agent_rs = {}
        _agent_rs["terminalId"] = terminal_id
        await db.execute(
            "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
            (json.dumps(_agent_rs), now, agent_id),
        )

    await db.execute(
        """
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            owner_bridge_id = ?,
            terminal_id = ?,
            terminal_status = 'starting',
            terminal_command = ?,
            terminal_workspace = ?,
            -- Spawning a NEW managed PTY for this session IS the "backing (re)started" event:
            -- promote a dead-state denorm back to running, else the row keeps the previous
            -- backing's 'stopped' and the Console label reads "Console stopped" for a live
            -- attached terminal forever (cms-manager, 2026-06-10 — the lazy auto-start-on-send
            -- bound a fresh PTY to a session left 'stopped' by the old backing's death; the
            -- display deriver deliberately never promotes, so the bind moment must).
            status = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                          THEN 'running' ELSE status END,
            ended_at = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                            THEN NULL ELSE ended_at END,
            last_seen = ?
        WHERE id = ?
        """,
        (bridge_id, terminal_id, command, workspace, now, session["id"]),
    )
    return await _active_terminal_for_agent(db, agent_id, settings=settings)




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






# _render_pending_dispatch_item moved to service/api_core/dispatch_text.py in v0.5.4.


def _pending_dispatch_count(body: str) -> int:
    text = str(body or "")
    if text.startswith(_MERGED_DISPATCH_HEADER):
        return len(re.findall(r"^=== ITEM \d+ ===$", text, flags=re.MULTILINE))
    return 1 if text.strip() else 0


# _build_pending_dispatch_subject moved to service/api_core/dispatch_text.py in v0.5.4.


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
    # Keep queued merge ownership scoped to one sender. Cross-sender merge
    # loses the contract owner and makes handoff replies go to the wrong agent.
    cursor = await db.execute(
        """
        SELECT *
        FROM dispatch_runs
        WHERE target_agent = ?
          AND from_agent = ?
          AND status = 'queued'
        ORDER BY requested_at ASC
        LIMIT 1
        """,
        (recipient_id, from_agent),
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


# How many times a claimed-but-never-delivered run may be rescued before we accept that it
# is genuinely undeliverable and let it fail. Bounded on purpose: an unbounded prefer-recovery
# rule turns a dead run into an immortal one, which is the strand class DECISIONS.md warns
# about ("delivery gates read raw turn_busy, bounded by exactly one ceiling"). Counted from the
# run's OWN `requeued_orphaned_claim` events, so the bound needs no schema and survives a
# restart.
# UNDELIVERED_CLAIM_REQUEUE_LIMIT moved to service/api_core/recovery_writes.py in v0.5.4 with the
# rescue it bounds — the difference between a rescue and an infinite loop.


# _requeue_instead_of_failing_undelivered_claim moved to service/api_core/recovery_writes.py in v0.5.4.


async def _fail_stale_active_run(
    db,
    active_run: dict[str, Any],
    *,
    reason: str,
    summary: str,
    event_body: str,
) -> bool:
    run_id = str(active_run.get("runId") or "").strip()
    if not run_id:
        return False
    if await _requeue_instead_of_failing_undelivered_claim(db, run_id, reason=reason):
        return True
    target_cursor = await db.execute("SELECT target_agent FROM dispatch_runs WHERE id = ?", (run_id,))
    target_row = await target_cursor.fetchone()
    target_agent = str((target_row["target_agent"] if target_row else "") or "").strip()
    finished_at = _now()
    await db.execute(
        "UPDATE dispatch_runs SET status = 'failed', summary = ?, error_text = ?, finished_at = ? WHERE id = ?",
        (summary, reason, finished_at, run_id),
    )
    await _append_dispatch_event(db, run_id, "auto_heal", event_body)
    await _fail_pending_controls_for_run(
        db,
        run_id,
        handled_at=finished_at,
        response_text=reason,
    )
    if target_agent:
        await _invalidate_agent_live_state(db, target_agent)
    return True


async def _discard_unclaimable_active_run(db, recipient_id: str, active_run: dict[str, Any]) -> bool:
    """Fail active runs whose owner cannot possibly consume controls anymore.

    Steer controls are only useful while the owning bridge is current and
    heartbeating. If the environment is offline or the bridge row is stale, a
    normal send would otherwise appear successful while its control sits
    unclaimed forever.
    """
    owner_bridge_id = str(active_run.get("claimBridgeId") or "").strip()
    if not owner_bridge_id:
        if str(active_run.get("dispatchMode") or "").strip().lower() != "terminal":
            return False
        started_at = str(active_run.get("startedAt") or active_run.get("requestedAt") or "").strip()
        started_epoch = _iso_to_epoch(started_at)
        if not started_epoch:
            return False
        settings = await _load_settings(db)
        stale_seconds = max(300, int(settings.get("active_run_stale_minutes", 30) or 30) * 60)
        if time.time() - started_epoch <= stale_seconds:
            return False
        return await _fail_stale_active_run(
            db,
            active_run,
            reason=f"Active run has no owning bridge and has exceeded {stale_seconds}s.",
            summary="Active run failed because no bridge owner was recorded and no reply completed before the stale-run timeout.",
            event_body="Stale unowned active run cleaned by periodic repair.",
        )
    execution_mode = str(active_run.get("executionMode") or "").strip().lower()
    channel_owned = execution_mode == "channel"

    agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
    agent = await agent_cursor.fetchone()
    runtime_state = _json_loads_or(agent["runtime_state"], {}) if agent else {}
    current_agent_bridge = str(runtime_state.get("bridgeInstanceId") or "").strip()
    environment_id = str(runtime_state.get("environmentId") or "").strip()

    if agent and _normalize_session_mode(agent["session_mode"] or "resident") == "managed":
        if not environment_id:
            session_cursor = await db.execute(
                """
                SELECT environment_id
                FROM agent_sessions
                WHERE agent_id = ?
                ORDER BY last_seen DESC
                LIMIT 1
                """,
                (recipient_id,),
            )
            session = await session_cursor.fetchone()
            environment_id = str((session["environment_id"] if session else "") or "").strip()
        if environment_id:
            settings = await _load_settings(db)
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
            env = await env_cursor.fetchone()
            env_status = _environment_effective_status(
                env,
                offline_seconds=settings.get("environment_offline_seconds", 90),
            ) if env else "offline"
            env_bridge = str((env["bridge_id"] if env else "") or "").strip()
            if env_status not in {"online", "degraded"}:
                return await _fail_stale_active_run(
                    db,
                    active_run,
                    reason=f'Managed environment "{environment_id}" is {env_status}; active run owner bridge "{owner_bridge_id}" can no longer receive controls.',
                    summary=f'Active run failed because environment "{environment_id}" is {env_status}. Restart the environment bridge and retry.',
                    event_body=f"Stale active run cleaned before send: environment {environment_id} is {env_status}",
                )
            if env_bridge and env_bridge != owner_bridge_id and not channel_owned:
                return await _fail_stale_active_run(
                    db,
                    active_run,
                    reason=f'Active run owner bridge "{owner_bridge_id}" is not the current environment bridge "{env_bridge}".',
                    summary=f'Active run failed because bridge "{owner_bridge_id}" was replaced by "{env_bridge}". Retry after the current bridge is stable.',
                    event_body=f"Stale active run cleaned before send: {owner_bridge_id} -> {env_bridge}",
                )

    if current_agent_bridge and current_agent_bridge != owner_bridge_id and not channel_owned:
        # Scope-narrow: don't fail the run just because the agent's stored
        # bridgeInstanceId changed. With same-logical-owner re-register
        # (slice 4dbb2e2) the prior bridge stays NOT-superseded; it's still
        # a valid owner. Only fail when the owner bridge has actually been
        # superseded — that's a real ownership change.
        owner_state_cursor = await db.execute(
            "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
            (owner_bridge_id, recipient_id),
        )
        owner_state = await owner_state_cursor.fetchone()
        owner_is_superseded = bool(owner_state and str(owner_state["superseded_by"] or "").strip())
        if owner_is_superseded:
            return await _fail_stale_active_run(
                db,
                active_run,
                reason=f'Active run owner bridge "{owner_bridge_id}" is not the current agent bridge "{current_agent_bridge}".',
                summary=f'Active run failed because bridge "{owner_bridge_id}" was replaced by "{current_agent_bridge}". Retry after the current bridge is stable.',
                event_body=f"Stale active run cleaned before send: {owner_bridge_id} -> {current_agent_bridge}",
            )

    bridge_cursor = await db.execute(
        "SELECT last_seen FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (owner_bridge_id, recipient_id),
    )
    bridge = await bridge_cursor.fetchone()
    bridge_last_seen = _iso_to_epoch((bridge["last_seen"] if bridge else "") or "")
    if bridge and bridge_last_seen and time.time() - bridge_last_seen > ACTIVE_RUN_BRIDGE_STALE_SECONDS:
        return await _fail_stale_active_run(
            db,
            active_run,
            reason=f'Active run owner bridge "{owner_bridge_id}" has not heartbeated for more than {ACTIVE_RUN_BRIDGE_STALE_SECONDS}s.',
            summary=f'Active run failed because bridge "{owner_bridge_id}" stopped heartbeating. Restart the bridge and retry.',
            event_body=f"Stale active run cleaned before send: bridge heartbeat expired for {owner_bridge_id}",
        )

    return False


async def _discard_unusable_active_run(db, recipient_id: str, active_run: dict[str, Any]) -> bool:
    if await _discard_superseded_active_run(db, recipient_id, active_run):
        return True
    return await _discard_unclaimable_active_run(db, recipient_id, active_run)




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
    placeholders = ",".join("?" for _ in message_ids)
    cursor = await db.execute(
        f"SELECT id FROM messages WHERE id IN ({placeholders})",
        message_ids,
    )
    existing_ids = {str(existing["id"]) for existing in await cursor.fetchall()}
    if not existing_ids:
        return 0
    for message_id in message_ids:
        if message_id not in existing_ids:
            continue
        await db.execute(
            "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (message_id, agent_id, read_at),
        )
    return len(existing_ids)


# _dispatch_conversation_context moved to service/routers/dispatch_messages/shared.py in v0.5.3.




# _is_replaceable_auto_handoff_message moved to service/routers/dispatch_messages/shared.py in v0.5.3.


# _HANDOFF_REPLY_TYPES moved to service/api_core/reply_contract.py in v0.5.4 with its
# only reader, _message_satisfies_reply_contract (sole-reader move).
# _COMPLETION_INFO_RE moved to service/api_core/reply_contract.py in v0.5.4 with its
# only reader, _message_satisfies_reply_contract (sole-reader move).


# _message_satisfies_reply_contract moved to service/api_core/reply_contract.py in v0.5.4.


# _clear_turn_busy_if_no_open_reply_owing_run moved to service/api_core/turn_state.py in v0.5.4.


async def _mark_dispatch_run_answered(
    db,
    run_id: str,
    reply_message_id: str,
    current_status: str = "",
    execution_mode: str = "",
):
    status = str(current_status or "").strip().lower()
    mode = str(execution_mode or "").strip().lower()
    target_cursor = await db.execute("SELECT target_agent, dispatch_mode FROM dispatch_runs WHERE id = ?", (run_id,))
    target_row = await target_cursor.fetchone()
    target_agent = str((target_row["target_agent"] if target_row else "") or "").strip()
    dispatch_mode = str((target_row["dispatch_mode"] if target_row and "dispatch_mode" in target_row.keys() else "") or "").strip().lower()
    if (
        status in {"queued", "delivered"}
        or (mode in {"channel", "resident"} and status in {"claimed", "running"})
        or (dispatch_mode == "terminal" and status in {"claimed", "running"})
    ):
        await db.execute(
            """
            UPDATE dispatch_runs
            SET result_message_id = ?,
                status = 'completed',
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ?
            """,
            (reply_message_id, _now(), run_id),
        )
        # Event-based working-state clear. claude-channel.js pulses
        # turn_busy=true on every delivery and relies on the 120s
        # TURN_BUSY_STALE_SECONDS window for cleanup. That window is too
        # long after the agent's reply lands — operator sees "working"
        # linger when the actual work is done. Clear it here for any
        # channel-or-resident dispatch that just got answered AND has
        # no other in-flight rr=1 runs for the same agent (so we don't
        # clear while real reply-owing work is still in flight).
        if mode in {"channel", "resident"} and target_agent:
            await _clear_turn_busy_if_no_open_reply_owing_run(db, target_agent, run_id)
        await _invalidate_agent_live_state(db, target_agent)
        return
    await db.execute(
        "UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?",
        (reply_message_id, run_id),
    )
    await _invalidate_agent_live_state(db, target_agent)




_UNTHREADED_HANDOFF_WINDOW_MS = 24 * 60 * 60 * 1000






# _link_unthreaded_completion_message_for_run moved to service/reconcilers/managed_workers.py in v0.5.3.


# _auto_handoff_subject_for_run moved to service/api_core/dispatch_text.py in v0.5.4.


# _is_provider_rate_limit_error moved to service/api_core/dispatch_text.py in v0.5.4.


def _auto_handoff_body_for_run(row) -> str:
    status = str((row["status"] if row else "") or "").strip().lower()
    from_agent = str((row["from_agent"] if row else "") or "").strip()
    if status == "failed":
        detail = str((row["error_text"] if row else "") or (row["summary"] if row else "") or "Run failed.").strip()
        if _is_provider_rate_limit_error(detail):
            # Sender-facing notice (2026-06-07): a provider rate/usage limit is transient and
            # NOT the sender's fault — say so plainly so they retry instead of assuming the
            # recipient ignored them. Flows through the existing auto-handoff delivery.
            who = str((row["target_agent"] if row else "") or "").strip() or "The agent"
            note = (
                f"⚠️ {who} couldn't respond — its model provider is rate-limiting / at a usage "
                "limit right now (a provider-side throttle, not your request). Please retry shortly."
            )
            return f"{note}\n\n{detail}"
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
        return detail
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
_REAP_TRIAD_BODY_SENTINEL = "__aify_reap_triad__"






















# _touch_current_agent_session moved to service/api_core/agent_sessions.py in v0.5.4.




# ─── Messages ────────────────────────────────────────────────────────────────

def _reject_sender_truncated_body(body):
    if re.search(r"(?:\.\.\.|…)\[truncated\](?:\s*```)?\s*$", str(body or ""), re.I):
        raise HTTPException(
            422,
            "Message body was already truncated by the sender; resend a complete concise body or link a durable artifact.",
        )










# ─── Agent Info ──────────────────────────────────────────────────────────────



async def _adopt_live_resident_driver(db, agent_id: str) -> bool:
    """SELF-HEAL for the launch-terminal-first / switch-second ordering (2026-06-12,
    sc-manager strand): a channel sidecar claiming/beating for a RESIDENT-mode agent with
    driver_state != 'driving' is only a DISPLACED MANAGED driver when no live resident
    session exists. When a FRESH resident bridge row is beating, this sidecar IS that live
    resident session's own delivery path — the operator launched the resident terminal
    FIRST (registration set driver_state='driving') and clicked "switch to resident"
    SECOND, and the switch clobbered driver_state back to 'idle'. Releasing the sidecar
    then silently killed resident delivery: sends reported "sent", runs queued forever,
    nothing claimed. Adopt the driving state instead of releasing. Returns True when
    adopted (caller skips the release)."""
    # bridge_kind is '' on a registration-created row (only heartbeats stamp the kind) —
    # accept that shape only when the bridge row itself was registered as a RESIDENT
    # session; a managed registration's kindless bridge must never count as a live
    # resident driver (it would re-adopt a genuinely displaced agent).
    row = await (await db.execute(
        "SELECT id FROM bridge_instances WHERE agent_id = ? "
        "AND (bridge_kind = 'resident' OR (COALESCE(bridge_kind, '') = '' AND session_mode = 'resident')) "
        "AND COALESCE(superseded_by, '') = '' AND datetime(last_seen) > datetime('now', '-150 seconds') "
        "LIMIT 1",
        (agent_id,),
    )).fetchone()
    if not row:
        return False
    await db.execute("UPDATE agents SET driver_state = 'driving' WHERE id = ?", (agent_id,))
    return True









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




def _contract_reminder_due(
    row,
    *,
    settings: dict[str, Any],
    now_s: Optional[float] = None,
    ignore_repeat: bool = False,
) -> tuple[bool, str]:
    if not settings.get("reply_contracts_enabled", True):
        return False, "reply contract reminders are disabled"
    state = _contract_state(row, settings=settings, now_s=now_s)
    if not state["overdue"]:
        return False, f'contract state is {state["state"]}'
    max_count = max(0, int(settings.get("reply_reminder_max_count", 0) or 0))
    if max_count and state["reminderCount"] >= max_count:
        return False, f"max reminders reached ({state['reminderCount']}/{max_count})"
    last_reminder_at = str((row["last_reminder_at"] if row and "last_reminder_at" in row.keys() else "") or "").strip()
    if last_reminder_at and not ignore_repeat:
        repeat_minutes = max(1, int(settings.get("reply_reminder_repeat_minutes", DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]) or DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]))
        last_s = _iso_to_epoch(last_reminder_at)
        if last_s and ((now_s or time.time()) - last_s) < repeat_minutes * 60:
            return False, f"last reminder was less than {repeat_minutes} minutes ago"
    return True, ""


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







