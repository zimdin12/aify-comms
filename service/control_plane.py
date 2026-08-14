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
import json
import sqlite3
import time


# _listen_events moved to service/longpoll.py in v0.5.4 with `_wake_agent` — that module already
# owned the other waiter registry, and the identity gate names it as the sole owner.

from service.api_core.status_decision import StatusFacts, _decide_effective_status
from service.config import get_config
# v0.5.2a: the shared route class lives with the domain-router factory so no domain can build a
# router without the SQLite lock-retry. See service/api_core/routing.py.
from service.api_core.validation import SAFE_NAME_RE, validate_name  # v0.5.1f: one owner
from service.api_core.runtime import (  # v0.5.1e: single owner, resolved against the contract
    _normalize_runtime,
)
from service.api_core.status_inputs import (
    _compute_live_status_cache,
    _gather_status_inputs,
)
from service.db import get_db
from service.terminal_snapshot import render_live_screen as _render_live_terminal_screen
# v0.5 slice 1a. The status cache and the bridge reconcilers now live in their own module.
#
# FUNCTIONS are imported by name — safe, because a function object is never rebound. The CACHE DICT
# is deliberately NOT: `from ... import _LIVE_STATE_CACHE` would bind this module to whatever object
# existed at import time, and a later rebind in the owner would leave two dicts with reads and
# writes landing in different ones — silently. Reach it as `status_cache._LIVE_STATE_CACHE`.
# `service/tests/test_process_global_identity.py` fails the suite if that rule is broken.
from service.api_core.recovery_writes import (  # v0.5.4: moved out; the control plane is now a CALLER
    _requeue_instead_of_failing_undelivered_claim,
)
# v0.5.4: the whole prompt-hint group moved to terminal_text.py, taking `_ANSI_RE` and
# `_terminal_awaiting_input_hint` with it -- they had no other reader here.
from service.api_core.managed_env import (  # v0.5.4: moved out; the control plane is now a CALLER
    _managed_console_is_booting,
)
from service.api_core.liveness import (  # v0.5.4: moved out; the control plane is now a CALLER
    ACTIVE_RUN_BRIDGE_STALE_SECONDS,
)
from service.env_status import _ENVIRONMENT_HEARTBEAT_STATUSES
# v0.5 slice 2: the spawn-lifecycle reconcilers moved to their own module.
from service.reconcilers.dispatch_lifecycle import (
    _close_orphaned_managed_runs,
    _fail_stranded_delivered_reply_runs,
    _sweep_unmirrored_failed_handoffs,
)
from service.reconcilers.dispatch_queue import (
    _close_reconcilable_delivered_runs,
    _reap_undeliverable_queued_runs,
    _replay_undelivered_channel_messages_on_env_recovery,
    _requeue_orphaned_claimed_runs,
    _reroute_orphaned_managed_channel_runs,
)
from service.reconcilers.spawn_lifecycle import (
    _fail_running_spawns_superseded_by_current_session,
    _finalize_spawns_with_dead_terminals,
)
from service.reconcilers.status_cache import (
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
from service.api_core.dispatch_text import (  # v0.5.4: moved out; the carrier is a CALLER
    _pending_dispatch_count,
)
from service.api_core.active_run_discard import (  # v0.5.4: moved out; the carrier is a CALLER
    _discard_unclaimable_active_run,
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




# _managed_environment_status moved to service/api_core/managed_env.py in v0.5.4.


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


# _terminal_control_to_dict moved to service/api_core/terminal_controls_io.py in v0.5.4.
# (It went to service/routers/terminals.py first, in v0.5.3, and on to the leaf when that router
# gave up its three non-route declarations. Two hops, one pointer — the gate that names the CURRENT
# owner is what caught the stale one.)


# _trim_terminal_output moved to service/routers/terminals.py in v0.5.3, then on to
# service/api_core/terminal_output.py in v0.5.4.


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


# _MERGED_DISPATCH_HEADER moved to service/api_core/dispatch_text.py in v0.5.4.
# _MERGED_DISPATCH_FOOTER moved to service/api_core/dispatch_text.py in v0.5.4.
# _DISPATCH_BUFFER_CAP moved to service/api_core/dispatch_buffer.py in v0.5.4.


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




# _link_unthreaded_completion_message_for_run moved to service/reconcilers/managed_workers.py in v0.5.3.


# _auto_handoff_subject_for_run moved to service/api_core/dispatch_text.py in v0.5.4.


# _is_provider_rate_limit_error moved to service/api_core/dispatch_text.py in v0.5.4.


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


# ─── Dispatch Runs ────────────────────────────────────────────────────────────


# _agent_has_live_claimer moved to service/reconcilers/dispatch_queue.py in v0.5.3.


# _mirror_undeliverable_queued_run_to_sender moved to service/reconcilers/dispatch_queue.py in v0.5.3.


# _contract_list_query moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_reminder_due moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_reminder_full_every moved to service/api_core/reply_contract.py in v0.5.4.


# _contract_reminder_body moved to service/api_core/reply_contract.py in v0.5.4.


# ─── Shared Artifacts ────────────────────────────────────────────────────────


# ─── Channels ────────────────────────────────────────────────────────────────


# ─── Settings ────────────────────────────────────────────────────────────────


# ─── Stats ───────────────────────────────────────────────────────────────────


# ─── Clear ───────────────────────────────────────────────────────────────────


# ─── Rotate ──────────────────────────────────────────────────────────────────


# ─── Dashboard compatibility redirects ──────────────────────────────────────

