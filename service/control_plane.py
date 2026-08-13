"""The live control plane: the helpers, constants and queues the route domains share.

25 helpers and the constants behind status, dispatch, terminals, spawn and console. It declares NO
routes and owns no router — `service/routers/api_v2.py` is the composition surface, and it is 53
lines of `include_router` with no re-export of anything here, so a stale
`from service.routers.api_v2 import <helper>` fails loudly instead of quietly resolving.

THE COUNTS ABOVE ARE MEASURED, and were wrong for a while: this said "~140 helpers, two queue
classes" after the queues had moved to `service/terminal_write_queue.py` and most of the helpers had
followed the reconcilers and route domains out. Prose written beside a move describes the plan;
nothing in the suite reads prose. Re-measure before editing this paragraph.

This file was `service/routers/api_v2.py`, 20,545 lines at its peak, until v0.5 moved the
reconcilers out and v0.5.2 moved the route domains out. By the end of that it declared zero routes:
a helper library living at a router's address. v0.5.3 moved it here and left the composition behind.

Its header until then still read "aify-comms v2 API — drop-in replacement for api.py", describing a
migration finished long before any of this. That was worth fixing rather than carrying: a file this
central whose first three lines are wrong teaches every reader something false before they reach the
code.

IT IS STILL TOO BIG, and what is left is no longer a pile of small helpers: 25 functions hold ~1,450
lines, and the four largest hold ~950 of them. Splitting those is extract-method, not relocation —
gated by `service/tests/extract_method.py` — and a v0.6 question. Until then: put NEW behaviour in a
leaf (`service/api_core/`, `service/reconcilers/`, `service/status_engine.py`) and import it.

DO NOT LEAVE AN IMPORT BEHIND WHEN YOU MOVE SOMETHING OUT. In v0.5.4 this file carried 309 import
bindings of which 180 were reached by nothing at all — one orphaned per extraction, accumulated over
the whole series, plus every request model from `service.models` for routes that left in v0.5.2. They
cost 148 lines and made the file look coupled to two dozen modules it does not use.
"""
import asyncio
import json
import sqlite3
import re
import time
import uuid
from typing import Any, Optional

from fastapi import Request

# Per-agent wake-up events for comms_listen
_listen_events: dict[str, asyncio.Event] = {}

from service.api_core.status_decision import StatusFacts, _decide_effective_status
from service.config import get_config
from service.api_core.dispatch_run_state import _append_dispatch_control, _finalize_dispatch_runs
from service.api_core.dispatch_text import _auto_handoff_body_for_run
from service.api_core.active_run_lookup import (
    _find_mergeable_queued_run,
)
from service.api_core.managed_env import _managed_environment_unavailable_reason
from service.api_core.events import (
    _append_dispatch_event,
)
# v0.5.2a: the shared route class lives with the domain-router factory so no domain can build a
# router without the SQLite lock-retry. See service/api_core/routing.py.
from service.api_core.ws import _get_ws  # v0.5.1h: accessor only; manager stays on app.state
from service.api_core.settings import _load_settings
from service.api_core.validation import SAFE_NAME_RE, validate_name  # v0.5.1f: one owner
from service.api_core.runtime import (  # v0.5.1e: single owner, resolved against the contract
    _normalize_runtime,
    _normalize_session_mode,
)
from service.api_core.serialization import (  # v0.5.1c: single owner, no copy
    _json_loads_or,
    _row_require_reply,
)
from service.api_core.claim_gating import _dispatch_message_id_for_recipient
from service.api_core.status_refresh import (
    _compute_agent_status,
    _refresh_agent_live_state,
    _refresh_expired_agent_live_states,
)
from service.api_core.status_inputs import (
    _compute_live_status_cache,
    _gather_status_inputs,
    engine_status,
)
from service.db import get_db
from service.terminal_snapshot import render_live_screen as _render_live_terminal_screen
from service.clock import now as _now
# v0.5 slice 1a. The status cache and the bridge reconcilers now live in their own module.
#
# FUNCTIONS are imported by name — safe, because a function object is never rebound. The CACHE DICT
# is deliberately NOT: `from ... import _LIVE_STATE_CACHE` would bind this module to whatever object
# existed at import time, and a later rebind in the owner would leave two dicts with reads and
# writes landing in different ones — silently. Reach it as `status_cache._LIVE_STATE_CACHE`.
# `service/tests/test_process_global_identity.py` fails the suite if that rule is broken.
from service.env_status import environment_effective_status as _environment_effective_status
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.turn_state import (  # v0.5.4: moved out; the control plane is now a CALLER
    _turn_busy_state,
)
from service.api_core.channel_delivery import (  # v0.5.4: moved out; the control plane is now a CALLER
    _apply_channel_routing_to_claude_runs,
)
from service.api_core.recovery_writes import (  # v0.5.4: moved out; the control plane is now a CALLER
    _requeue_instead_of_failing_undelivered_claim,
)
# v0.5.4: the whole prompt-hint group moved to terminal_text.py, taking `_ANSI_RE` and
# `_terminal_awaiting_input_hint` with it -- they had no other reader here.
from service.api_core.managed_env import (  # v0.5.4: moved out; the control plane is now a CALLER
    _managed_console_is_booting,
)
from service.api_core.liveness import (  # v0.5.4: moved out; the control plane is now a CALLER
    _resident_bridge_is_fresh,
    ACTIVE_RUN_BRIDGE_STALE_SECONDS,
    _has_live_terminal_session,
)
from service.api_core.reply_contract import (  # v0.5.4: moved out; the control plane is now a CALLER
    _contract_list_query,
    _contract_reminder_body,
    _contract_reminder_is_full,
)
from service.api_core.dispatch_text import (  # v0.5.4: moved out; the control plane is now a CALLER
    _auto_handoff_subject_for_run,
    _build_pending_dispatch_subject,
)
from service.api_core.records import (
    # v0.5.4: moved out; the control plane is now a CALLER,
    _agent_record_to_dict,
    _row_status_note,
    _status_with_dispatch,
)
from service.api_core.capabilities import (  # v0.5.4: moved out; the control plane is now a CALLER
    _has_live_rpc_controller,
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
    _fail_orphaned_running_spawn_requests,
    _fail_running_spawns_superseded_by_current_session,
    _finalize_spawns_with_dead_terminals,
    _repair_spawn_requests_from_initial_dispatch_failures,
)
from service.reconcilers.status_cache import (
    _live_state_fresh,
    _live_state_get,
    _prune_superseded_bridges,
    _reap_stale_orphan_bridges,
)
from service.models import (
    SpawnRequestClaim,
)

# _WINDOWS_DRIVE_CWD_RE moved to service/api_core/registration_gates.py in v0.5.4 —
# zero carrier readers, every consumer was a borrow accessor.
# _WSL_DRIVE_CWD_RE moved to service/api_core/registration_gates.py in v0.5.4 —
# zero carrier readers, every consumer was a borrow accessor.

# logger moved to service/api_core/status_refresh.py in v0.5.4 with `_refresh_agent_live_state`,
# which held the only logger call left here. The NAME `aify_comms.api_v2` went with it unchanged:
# operators filter logs by it, so it is contract, not an implementation detail.

# The VIRTUAL_*_RPC_COMMAND sentinels and their map/set moved to
# service/api_core/virtual_rpc.py in v0.5.4 — a neutral leaf, because five
# unrelated subsystems compare against them.



# v0.5.3: the ROUTER COMPOSITION that used to live here moved to service/routers/api_v2.py,
# which is now nothing but composition. This module is the control plane: helpers, constants
# and the two queue classes. It declares no routes and owns no router.
from service.api_core.dispatch_state import (  # v0.5.4: moved out; the carrier is a CALLER
    _DISPATCH_TERMINAL_STATUSES,
    _is_delivery_only_claude_run,
)
from service.api_core.dispatch_text import (  # v0.5.4: moved out; the carrier is a CALLER
    _pending_dispatch_count,
)
from service.api_core.execution_mode import (  # v0.5.4: both moved out of this file
    _agent_execution_mode,
    _auto_return_resident_to_managed_if_possible,
)
from service.api_core.active_run_discard import (  # v0.5.4: moved out; the carrier is a CALLER
    _discard_unclaimable_active_run,
    _discard_unusable_active_run,
    _fail_stale_active_run,
)
from service.terminal_write_queue import (  # v0.5.4: moved out; the control plane is now a CALLER
    TERMINAL_OUTPUT_WRITES,
    TerminalOutputWriteQueue,
)  # noqa: E402
# v0.5.4: was imported from service.routers.terminals. The carrier reaching a LEAF through a
# ROUTER is the dependency direction this slice exists to reverse — leaving it would have kept
# the queue blocked while looking fixed.




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





# _machine_family moved to service/routers/agents/shared.py in v0.5.3, then on to service/api_core/registration_gates.py in v0.5.4 — the agents package was its
# only consumer once the domains moved, so the borrow shim became the last thing keeping it here.












# _is_delivery_only_claude_run moved to service/api_core/dispatch_state.py in v0.5.4.


# _dispatch_reply_state moved to service/api_core/reply_contract.py in v0.5.4.


# _dispatch_reply_pending moved to service/routers/dispatch_messages/shared.py in v0.5.3, then on to service/api_core/reply_contract.py in v0.5.4 — the
# dispatch+messages package was its only consumer. `_dispatch_reply_state`, which it calls, is still
# router-owned and stays borrowed there.


# _is_operator_closed_contract moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_reply_expected moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_state moved to service/api_core/reply_contract.py in v0.5.4.






# _has_codex_live_app_server moved to service/api_core/capabilities.py in v0.5.4.


# _has_hermes_gateway_url moved to service/api_core/capabilities.py in v0.5.4.












# _agent_tombstone moved to service/api_core/agent_sessions.py in v0.5.4.


# _tombstone_agent moved to service/api_core/agent_sessions.py in v0.5.4.


# _remove_agent_record moved to service/api_core/agent_removal.py in v0.5.4.


# _default_capabilities_for moved to service/api_core/capabilities.py in v0.5.4.




# _row_capabilities moved to service/api_core/capabilities.py in v0.5.4.





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


# _turn_busy_holds_delivery moved to service/routers/dispatch_messages/shared.py in v0.5.3, then on
# to service/api_core/claim_gating.py in v0.5.4.




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


# _bridge_claim_block_reason moved to service/routers/dispatch_messages/shared.py in v0.5.3, then on
# to service/api_core/claim_gating.py in v0.5.4.






STUCK_STOPPING_GRACE_SECONDS = 900  # a 'stopping' PTY that never reached 'stopped' is wedged








# _record_channel_sidecar_heartbeat moved to service/api_core/recovery_writes.py in v0.5.4.




# _stop_virtual_terminals_for_superseded_bridges moved to service/routers/agents/shared.py in v0.5.3,
# then on to service/api_core/bridge_supersede.py in v0.5.4.


# _fail_active_runs_for_superseded_bridges moved to service/routers/agents/shared.py in v0.5.3, then
# on to service/api_core/bridge_supersede.py in v0.5.4.


# _fail_pending_controls_for_run moved to service/api_core/active_run_discard.py in v0.5.4.








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











# _terminal_pi_idle_prompt_hint moved to service/reconcilers/terminal_runs.py in v0.5.3.




LIST_AGENTS_REFRESH_LIMIT = 8




# _managed_environment_status moved to service/api_core/managed_env.py in v0.5.4.


from service.api_core.capabilities import (
    _row_capabilities,
)
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












# _terminal_status_transition moved to service/routers/terminals.py in v0.5.3, then on to
# service/api_core/terminal_status.py in v0.5.4.




# class TerminalOutputWriteQueue moved to service/terminal_write_queue.py in v0.5.4,
# with its singleton. It is not an api_core leaf: it owns its own transaction.


# TERMINAL_OUTPUT_WRITES moved to service/terminal_write_queue.py in v0.5.4 —
# the declaration must stay beside the class so a second instance cannot appear.


    await TERMINAL_OUTPUT_WRITES.flush_all()

# _release_stale_console_owner_for_claim moved to service/routers/dispatch_messages/shared.py in
# v0.5.3, then on to service/api_core/claim_gating.py in v0.5.4.


# _release_stale_terminal_owner moved to service/api_core/terminal_ownership.py in v0.5.4.


# _active_terminal_for_agent moved to service/api_core/terminal_ownership.py in v0.5.4.



# _has_pending_or_booting_spawn_request moved to service/api_core/managed_env.py in v0.5.4.


# _has_claimable_steerable_run moved to service/routers/dispatch_messages/shared.py in v0.5.3, then
# on to service/api_core/claim_gating.py in v0.5.4.


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








# _dispatch_source_message_ids moved to service/api_core/claim_gating.py in v0.5.4.


# _mark_dispatch_source_messages_read moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_conversation_context moved to service/routers/dispatch_messages/shared.py in v0.5.3,
# then on to service/api_core/claim_gating.py in v0.5.4.




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







