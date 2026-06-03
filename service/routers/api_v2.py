"""
aify-comms v2 API — SQLite backend.
Drop-in replacement for api.py with identical endpoint signatures.
"""
import asyncio
import json
import logging
import sqlite3
from collections import deque
import itertools
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError

# Per-agent wake-up events for comms_listen
_listen_events: dict[str, asyncio.Event] = {}

from service.db import get_db
from service.models import (
    AgentRegister, AgentStatusUpdate, AgentDescribeRequest, MessageSend, ClearRequest,
    ChannelCreate, ChannelMessage, ChannelJoin,
    AgentRuntimeStateUpdate, AgentSessionHandleUpdate, AgentSessionResolveRequest, AgentReadyUpdate, AgentSessionModeSwitchRequest, AgentResidentLostRequest, ConversationClearRequest, DispatchRequest, DispatchClaimRequest, DispatchRunUpdate,
    DispatchControlRequest, DispatchControlClaimRequest, DispatchControlUpdate,
    EnvironmentHeartbeat, EnvironmentControlRequest, EnvironmentControlClaim, EnvironmentControlUpdate, EnvironmentRootsUpdate,
    AgentEnvironmentAssignRequest, AgentRenameRequest, SpawnRequestCreate, SpawnRequestClaim, SpawnRequestUpdate, SessionControlRequest, AgentControlRequest,
    ConsoleStartRequest, TerminalControlRequest, TerminalControlClaim, TerminalControlUpdate, TerminalDeadReport, TerminalOutputRequest,
    VirtualTerminalEnsureRequest, AgentFavoriteUpdate, AgentConsoleInputRequest,
)

SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$')
_WINDOWS_DRIVE_CWD_RE = re.compile(r"^[a-zA-Z]:/")
_WSL_DRIVE_CWD_RE = re.compile(r"^/mnt/[a-zA-Z](?:/|$)")
_CONTROL_ID_COUNTER = itertools.count()

logger = logging.getLogger("aify_comms.api_v2")


def validate_name(name: str, label: str = "name") -> None:
    if not SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores.")


class JsonApiRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_route_handler(request: Request):
            try:
                return await original_handler(request)
            except (HTTPException, RequestValidationError):
                raise
            except sqlite3.OperationalError as error:
                message = str(error) or "database operation failed"
                locked = "locked" in message.lower() or "busy" in message.lower()
                status_code = 503 if locked else 500
                logger.warning(
                    "DB OperationalError on %s %s: %s", request.method, request.url.path, message
                )
                return JSONResponse(
                    status_code=status_code,
                    content={"ok": False, "error": f"Database temporarily unavailable: {message}"},
                )
            except Exception as error:
                # Never silently swallow an unexpected error into a tidy 500 —
                # that is exactly what makes production incidents undebuggable.
                logger.exception(
                    "Unhandled error on %s %s", request.method, request.url.path
                )
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": str(error) or error.__class__.__name__},
                )

        return custom_route_handler


router = APIRouter(tags=["api"], route_class=JsonApiRoute)

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _iso_to_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def _iso_from_ms(timestamp_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(0, int(timestamp_ms or 0)) / 1000))

def _shared_dir(request: Request) -> Path:
    try:
        d = Path(request.app.state.config.data_dir) / "shared_files"
    except Exception:
        d = Path("/data/shared_files")
    d.mkdir(parents=True, exist_ok=True)
    return d

_MANUAL_STATUSES = {"stopped"}

DEFAULT_SETTINGS = {
    "retention_days": 90,
    "max_messages_per_agent": 1000,
    "max_shared_size_mb": 500,
    "stale_agent_hours": 24,
    "dashboard_refresh_seconds": 15,
    "rotation_enabled": True,
    "idle_minutes": 5,
    "offline_minutes": 30,
    "environment_offline_seconds": 90,
    "reply_contracts_enabled": True,
    "reply_reminder_minutes": 10,
    "reply_reminder_repeat_minutes": 10,
    # Cap the number of reply reminders per unanswered require_reply run so an
    # owing agent is never nagged forever (runtime-agnostic governance). A
    # sane non-zero default bounds out-of-the-box behaviour; an operator can
    # set 0 to explicitly opt into unlimited reminders.
    "reply_reminder_max_count": 3,
    "contract_stale_hours": 24,
    "active_run_stale_minutes": 30,
    # Tighter cleanup window for managed dispatches. Default 5 min.
    # A managed run with an empty claim_bridge_id that hasn't progressed
    # within this window is presumed orphaned — the bridge crashed
    # between claim and the controller's failure-PATCH, OR the failure
    # PATCH hit a transient connection error and was logged-but-lost.
    # Tuned tighter than the 30-min generic terminal window because
    # managed dispatches are typically per-turn and shouldn't linger.
    "active_managed_run_stale_minutes": 5,
    # Absolute wall-clock ceiling for a claimed/running managed run, applied
    # REGARDLESS of bridge liveness. Catches the case where the owning bridge
    # keeps heartbeating but the inner controller died without PATCHing the run
    # terminal — the bridge-liveness reaper above never fires, so the agent is
    # pinned `working` forever. Keyed on real staleness (no progress events +
    # started/claimed age) so genuinely-progressing runs are never aged out.
    "active_managed_run_wall_ceiling_minutes": 30,
    # WS3 Task 3.2 (2026-06-02): backstop for `queued` dispatch_runs that no
    # reaper otherwise covers. A queued run whose target has NO live claimer
    # (no fresh channel-sidecar AND no claiming bridge) and is older than this
    # is never deliverable — it would pile up to the buffer cap and hard-reject
    # future sends (buffer_full). After this window such a run is FAILED with an
    # actionable error and mirrored back to the sender. A queued run WITH a live
    # claimer is left alone (it will be claimed on the next poll). Default 180s
    # comfortably exceeds the claim poll cadence + lazy-autostart-on-claim spawn.
    "queued_run_backstop_seconds": 180,
    # WS4 Task 4.3: TTL before a TERMINAL dispatch_run whose endpoints have no
    # live owner (tombstoned/removed/unknown target AND from) is pruned. Keeps a
    # just-removed agent's recent audit history briefly, then GCs it so removed
    # agents and test teardown don't accrete dispatch_runs forever. Never touches
    # non-terminal runs or any run referencing a currently-live agent.
    "orphaned_dispatch_run_retention_hours": 24,
    "managed_claude_model": "",
    "managed_claude_effort": "high",
    # Auto-confirm the Claude "WARNING: Loading development channels" prompt
    # when the bridge spawns a managed PTY or operator opens Console.
    # Default true: the prompt is just confirming behavior the operator
    # already asked for by launching a managed-channel claude wrapper.
    # Operators who want manual approval can flip false.
    "console_auto_confirm_claude_dev_channels": True,
    "managed_terminal_backing_enabled": True,
    # Universal delivery-mode flag (operator's design):
    #   false (default, the target architecture) — managed dispatch
    #     uses each runtime's proper delivery channel:
    #       * managed claude: aify-comms-channel notifications (the
    #         claude-channel.js MCP server inside the wrapper PTY
    #         claims and emits notifications/claude/channel events).
    #       * managed codex/pi/opencode/hermes: native RPC adapters
    #         (createCodexController, createPiController, etc.) via
    #         executionModes=["managed"] /dispatch/claim polling.
    #         No PTY-input typing.
    #   true (legacy / opt-in escape hatch) — bridge writes the
    #     dispatch body directly into the wrapper PTY as a
    #     bracketed-paste terminal_control. Operator-visible Console
    #     pop-up. Used as a working baseline when channel/RPC
    #     delivery is misconfigured or under investigation.
    #
    # Earlier name was claude_managed_channel_only and gated only the
    # claude-channel split. The current name covers ALL managed
    # runtimes and inverts the polarity so the proper-delivery path
    # is the default.
    "insert_messages_via_console": False,
    # Reply contract for managed/delivered runs. The intended model is:
    # aify-comms message in -> reply OUT via comms_send(inReplyTo=...) (a tool
    # call the agent makes); genuinely-direct terminal input -> direct output.
    # The injected prompt always directs agents to reply with comms_send.
    # This flag only controls the fallback when an agent finishes a delivered
    # run WITHOUT sending an explicit reply:
    #   True  (B, safety-net) — the bridge auto-mirrors the run summary back to
    #          the sender so the human/teammate still gets something.
    #   False (A, strict)     — no auto-mirror; the run stays reply-owed and the
    #          missing reply is surfaced rather than fabricated from final text.
    # Default True preserves prior behavior; set False to enforce strict
    # comms_send-only replies.
    "managed_reply_capture_fallback": True,
    # Slices 1/2/4 (proactive wrapper-PTY at spawn-request completion).
    # When true AND managed_terminal_backing_enabled is also true, the
    # service eagerly launches the agent's wrapper PTY at spawn-request
    # transition to "running" — the console pre-exists by the time the
    # first dispatch arrives, no "console pop-up on first send" UI
    # symptom, and subsequent dispatches reuse via slice-3's
    # console-attach reuse + the existing dispatch _active_terminal_for_agent.
    # Default true for normal dashboard operation: terminal-backed managed
    # agents should have an operator-visible Console before the first turn
    # needs it. Roll back by flipping this false.
    "managed_pty_eager_spawn": True,                    # Plan 4 (2026-05-25): auto-spawn on dispatch; was False
    # Unified-backing refactor (2026-05-24): when set, managed dispatches for
    # the listed runtimes route through a *-aify wrapper PTY (mirror of how
    # managed claude already works via claude-channel.js inside claude-aify)
    # instead of through the bridge's native RPC adapters (CodexSession,
    # HermesSession, HermesManagedGatewaySession, PiSession). The wrapper's
    # in-process MCP bridge claims /dispatch/claim and delivers via the
    # wrapper's local backing. Dashboard Session Console renders the real
    # Ink TUI of the wrapper via xterm.js.
    #   false: existing native managed dispatch flow.
    #   true: eligible codex / hermes managed runs route via wrapper.
    #   ["hermes", "codex"]: per-runtime opt-in during rollout.
    # claude-code is always wrapper-backed (claude-channel.js); not gated.
    # pi is structurally excluded (omp single-client RPC + bridge-owned
    # mutex make the wrapper pattern impossible). pi managed stays on
    # the persistent PiSession synth-terminal path. See DECISIONS.md.
    "managed_via_wrapper": ["codex", "hermes"],  # wrapper-backed default for Codex/Hermes; was False
    # Auto-close persistent workers (virtual rpc terminals) that have
    # been idle for this many minutes. 0 disables (default). Operator
    # asked for this 2026-05-22: after sending a message the agent
    # comes online (worker spawns), but if no follow-up arrives within
    # the window the worker should close to free resources and reflect
    # the actual operational state (available). The reconciler checks
    # every cycle (60s) and only acts on workers with no in-flight
    # dispatch_runs.
    "worker_idle_close_enabled": False,
    "worker_idle_close_minutes": 0,
    "managed_codex_model": "",
    "managed_codex_effort": "high",
    "managed_pi_model": "",
    "managed_pi_effort": "",
    "resident_lease_seconds": 150,
    # Show dashboard controls for explicit resident<->managed ownership
    # switches. The wrappers still auto-detect at launch; this governs only
    # visibility of the manual override controls.
    "manual_session_mode": True,
    "dashboard_title": "AIFY Comms",
    "dashboard_theme": "default",
    "dashboard_primary_color": "",
    "dashboard_secondary_color": "",
    "dashboard_tertiary_color": "",
}
_TERMINAL_MONOTONIC_STATUSES = {"stopping", "stopped", "failed", "lost", "ended", "completed", "cancelled"}
_TERMINAL_ACTIVE_STATUSES = {"starting", "attached", "running", "active", "idle"}
_RUNTIME_CONFIG_LIVE_KEYS = {
    "appServerUrl",
    "remoteAuthTokenEnv",
    "gatewayUrl",
    "gatewayTokenEnv",
    "channelEnabled",
}

_RUNTIME_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude_code": "claude-code",
    "codex": "codex",
    "hermes": "hermes",
    "hermes-agent": "hermes",
    "hermes_agent": "hermes",
    "oh-my-pi": "pi",
    "oh_my_pi": "pi",
    "opencode": "opencode",
    "omp": "pi",
    "pi": "pi",
    "pi-agent": "pi",
    "pi_agent": "pi",
    "generic": "generic",
}
_LAUNCHABLE_RUNTIMES = {"claude-code", "codex", "hermes", "opencode", "pi"}
_SESSION_MODES = {"resident", "managed"}
_DISPATCH_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_TERMINAL_END_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
_DISPATCH_ACTIVE_STATUSES = {"queued", "claimed", "running"}
_SPAWN_TERMINAL_STATUSES = {"running", "failed", "cancelled"}
_SESSION_DELETE_ALLOWED_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
_TERMINAL_DELETE_ALLOWED_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
# A session whose spawn/run is in flight or live. "starting" is included so a
# spawn-in-progress is not marked offline merely because the environment bridge
# instance id rotated (same rationale as a running session surviving a bridge
# restart); genuine staleness is still caught by env-offline/heartbeat checks.
_LIVE_SESSION_STATUSES = {"starting", "running", "recovering", "restarting", "cli-takeover"}
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
# ends, so this window now ONLY fires when an end event is DROPPED. It is kept at
# 120s as the CLAIM-GATE / mid-turn-busy window (the conservative choice: a send is
# not queued behind a possibly-finished turn longer than 2m). The STATUS staleness
# window is the longer TURN_BUSY_BACKSTOP_SECONDS so a missed end event self-heals
# at the single long wall-clock ceiling instead of flapping against the re-pulse
# cadence (the prior 120s-vs-45s race produced the false-working flap). Never key a
# re-arm of turn_busy on derived status — only the bridge sets it and only an event
# (or this backstop) clears it (anti-feedback-loop invariant).
TURN_BUSY_STALE_SECONDS = 120
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
# DECOUPLED from the short claim window below (#5 keeps the claim-gate at 120s so a
# queued send is never stranded behind a missed end-event).
#
# ANTI-FEEDBACK-LOOP: only a bridge/event sets turn_busy; only an event/this
# ceiling/the run-reply clear clears it. Status is NEVER read back to re-arm it.
TURN_BUSY_BACKSTOP_SECONDS = 30 * 60
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
_CHANNEL_MANAGED_RUNTIMES = {"claude-code"}
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
_CHANNEL_FLAG_GATED_RUNTIMES = {"hermes"}
# Claim-side whitelist for execution_mode='channel' runs. Claude channel
# claims can come from the claude-aify channel bridge. Wrapper-backed managed
# Codex/Hermes claims must come from the wrapper PTY child bridge registered
# as bridge_kind='managed-wrapper-child'; the main environment bridge is
# intentionally blocked from claiming them because it lacks the live local
# app-server/gateway for the visible console. opencode is intentionally
# excluded — its adapter declares preferred_delivery_mode='managed'.
_CHANNEL_CLAIM_RUNTIMES = _CHANNEL_MANAGED_RUNTIMES | {"codex", "hermes"}
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
_CHANNEL_SIDECAR_DELIVERY_RUNTIMES = {"claude-code", "hermes"}

def _managed_terminal_backing_enabled(settings: dict[str, Any]) -> bool:
    return bool(settings.get("managed_terminal_backing_enabled", DEFAULT_SETTINGS["managed_terminal_backing_enabled"]))


def _managed_via_wrapper_for_runtime(settings: dict[str, Any], runtime: str) -> bool:
    """True when managed dispatches for this runtime should route through a
    *-aify wrapper PTY (the wrapper's child bridge claims and delivers) instead
    of the bridge's native RPC adapter. Unified-backing refactor 2026-05-24,
    extended in Plan 2 (2026-05-25) to consult the runtime adapter.

    claude-code is excluded — it's already wrapper-backed via claude-channel.js
    inside claude-aify regardless of this flag.

    For all other runtimes, eligibility is driven by the adapter's
    preferred_delivery_mode == "managed-via-wrapper". Pi is explicitly
    excluded because OMP is single-client RPC; dashboard chat and Console must
    share the same native managed controller and virtual terminal stream.
    """
    from service.runtimes import adapter_for

    val = settings.get("managed_via_wrapper", DEFAULT_SETTINGS.get("managed_via_wrapper", False))
    runtime_n = _normalize_runtime(runtime or "")
    if runtime_n == "claude-code":
        return False
    if runtime_n == "pi":
        return False
    try:
        adapter = adapter_for(runtime_n)
    except ValueError:
        return False
    if adapter.preferred_delivery_mode != "managed-via-wrapper":
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, list):
        return runtime_n in {str(item).strip().lower() for item in val if item}
    return False


def _channel_flag_enabled(runtime_config: Any) -> bool:
    """True when the wrapper set the channel-enabled runtime flag
    (runtime_config.channelEnabled, exported as AIFY_CHANNELS_ENABLED=1)."""
    rc = runtime_config if isinstance(runtime_config, dict) else {}
    return bool(rc.get("channelEnabled"))


def _channel_managed_eligible(runtime: str, runtime_config: Any) -> bool:
    """Runtime-agnostic gate for the sidecar-channel managed delivery path —
    the channelEnabled-flag eligibility that lets a managed dispatch resolve to
    execution_mode='channel' even when the agent lacks the managed-run
    capability (the in-session sidecar delivers; the agent self-replies via
    comms_send; no headless managed-run API is used).

    Both claude (_CHANNEL_MANAGED_RUNTIMES) and hermes
    (_CHANNEL_FLAG_GATED_RUNTIMES) require the wrapper-set channelEnabled flag
    here — claude-aify and hermes-aify both export AIFY_CHANNELS_ENABLED=1, the
    SAME mechanism. This preserves the prior claude contract (no flag + no
    managed-run cap → rejected, no silent channel path) and extends it
    symmetrically to hermes.

    ASYMMETRY(hermes): claude is in _CHANNEL_MANAGED_RUNTIMES, so once it
    clears the cap check it ALWAYS routes to channel (no native managed-run);
    hermes routes to channel ONLY via this flag and otherwise keeps its native
    'managed' path. See the route decision in _agent_execution_mode.
    """
    runtime_n = _normalize_runtime(runtime or "")
    if runtime_n in _CHANNEL_MANAGED_RUNTIMES or runtime_n in _CHANNEL_FLAG_GATED_RUNTIMES:
        return _channel_flag_enabled(runtime_config)
    return False


async def _has_live_terminal_session(db, agent_id: str) -> bool:
    """Plan 4: True when this agent has a live terminal_session row
    (managed-via-wrapper path).

    Plan 5 follow-up (2026-05-26): synth/virtual terminals (id prefix
    `vterm_`) MUST NOT count as live for this check. Plan 4 deprecated
    synth terminals for wrapper-backed runtimes (see
    `_synth_terminal_should_be_created`), but pre-Plan-4 rows persist in
    operator DBs with `status='running'` and no cleanup path. Observed
    2026-05-26 — sc-coder, sc-architect kept showing `online` after Plan
    5 deploy because their stale 2026-05-24 `vterm_*` rows hid the dead
    worker from the gate.
    """
    if db is None:
        return False
    try:
        cursor = await db.execute(
            """
            SELECT COUNT(*) AS cnt FROM terminal_sessions
            WHERE agent_id = ?
              AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering')
              AND id NOT LIKE 'vterm_%'
            """,
            (agent_id,),
        )
        row = await cursor.fetchone()
        return bool(row and int(row["cnt"] or 0) > 0)
    except Exception:
        return False


def _has_live_rpc_controller(agent_id: str) -> bool:
    """Plan 4: True when an in-memory RPC controller is registered for this
    agent (managed-RPC synth fallback path). Today aify-comms doesn't
    maintain such a registry on the server side — the bridge owns RPC
    lifecycle. Returns False by default; wrapper-PTY backed agents go
    through _has_live_terminal_session above.

    Future: if a server-side registry of bridge-owned RPC children is
    introduced, query it here.
    """
    return False


# A channel-sidecar bridge heartbeat older than this is treated as a dead
# sidecar for deliverability/status purposes. The standalone sidecar's
# /dispatch/claim poll loop refreshes bridge_instances.last_seen on every tick
# (claim_dispatch: "the claim poll itself is the heartbeat"), so a live sidecar
# stays well within this window; a process that has exited goes stale quickly.
CHANNEL_SIDECAR_STALE_SECONDS = 180

# WS5 Task 5.1 (2026-06-02): an ACQUIRED claimer lease that has not been
# refreshed within this window is treated as stale (the loop died without
# POSTing `claimer-release` — e.g. SIGKILL / crash). The loop refreshes its
# lease on every successful /dispatch/claim round-trip (same cadence as the
# channel-sidecar heartbeat), so a live loop stays well inside this window.
# A clean `claimer-release` makes the lease not-live IMMEDIATELY (no wait);
# this window only backstops a MISSED release. Kept longer than the sidecar
# stale window so the lease is never the FIRST signal to expire on a live loop.
CLAIMER_LEASE_STALE_SECONDS = 240

# Workstream B2 (2026-06-01): grace before a managed claude with a LIVE sidecar
# but a DEAD console PTY is treated as a headless orphan worker. Must exceed the
# 30s liveness beat + console startup so a transiently-restarting console (PTY
# respawn between beats) is never falsely reaped.
MANAGED_ORPHAN_GRACE_SECONDS = 90


async def _has_live_channel_sidecar(db, agent_id: str) -> bool:
    """Task 1.6 (2026-05-30): True when a standalone channel sidecar
    (claude-channel.js for claude; the `hermes-managed-host.js run <agent>`
    gateway delivery loop for hermes) is currently heartbeating for this agent.

    This is the deliverability/liveness signal for runtimes whose managed wake
    is delivered by a standalone channel sidecar that owns NO wrapper PTY
    (hermes via its hidden `hermes dashboard --tui` gateway host). It is the
    runtime-agnostic equivalent of claude's `_has_live_terminal_session` gate:
    claude's sidecar runs INSIDE the claude-aify wrapper PTY (so a live PTY
    terminal_session is its liveness proof), whereas hermes's gateway loop is a
    separate process whose proof is its own `bridge_kind='channel-sidecar'`
    bridge_instances row with a fresh last_seen (kept fresh by the claim poll loop).

    Returns False when no such row exists (no sidecar ever ran), the row is
    superseded, or its heartbeat is older than CHANNEL_SIDECAR_STALE_SECONDS
    (the sidecar process died) — so status falls back to `available` instead of
    a falsely positive `online`.
    """
    if db is None:
        return False
    try:
        cursor = await db.execute(
            """
            SELECT last_seen FROM bridge_instances
            WHERE agent_id = ?
              AND bridge_kind = 'channel-sidecar'
              AND COALESCE(superseded_by, '') = ''
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        last_seen = _iso_to_epoch(str(row["last_seen"] or ""))
        if not last_seen:
            return False
        age = datetime.now(timezone.utc).timestamp() - last_seen
        return age <= CHANNEL_SIDECAR_STALE_SECONDS
    except Exception:
        return False


async def _has_live_managed_wrapper_child(db, agent_id: str) -> bool:
    """FIX SET B2 (2026-06-03): True when a live `managed-wrapper-child` bridge
    (the visible TUI's in-session aify-comms MCP, registered by a wrapper-backed
    managed worker — codex/hermes) is currently heartbeating for this agent.

    This is the deliverability signal a wrapper-backed managed 'channel' run needs:
    the run is rejected `managed_wrapper_child_required` until such a bridge claims
    it, so the send-path autostart must NOT treat a leftover RESIDENT-mode terminal
    as satisfying the managed coldstart. Returns False when no row exists, the row
    is superseded, or its heartbeat is older than ACTIVE_RUN_BRIDGE_STALE_SECONDS
    (mirrors _has_live_channel_sidecar's cutoff construction exactly)."""
    if db is None:
        return False
    try:
        cursor = await db.execute(
            """
            SELECT last_seen FROM bridge_instances
            WHERE agent_id = ?
              AND bridge_kind = 'managed-wrapper-child'
              AND COALESCE(superseded_by, '') = ''
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        last_seen = _iso_to_epoch(str(row["last_seen"] or ""))
        if not last_seen:
            return False
        age = datetime.now(timezone.utc).timestamp() - last_seen
        return age <= ACTIVE_RUN_BRIDGE_STALE_SECONDS
    except Exception:
        return False


async def _claimer_lease_row(db, agent_id: str):
    """WS5 Task 5.1: fetch the agent's single claimer-lease row (or None when no
    lease has EVER been recorded). Returns the raw row so callers can distinguish
    'no lease ever' (fall back to the sidecar/bridge check — lazy-claim contract)
    from 'lease present' (the lease is authoritative)."""
    if db is None:
        return None
    try:
        cursor = await db.execute(
            "SELECT agent_id, bridge_id, state, updated_at FROM claimer_leases WHERE agent_id = ?",
            (agent_id,),
        )
        return await cursor.fetchone()
    except Exception:
        return None


async def _has_live_claimer_lease(db, agent_id: str) -> bool:
    """WS5 Task 5.1 (2026-06-02): True when the agent has a currently-LIVE
    delivery-loop claimer lease.

    A lease is the POSITIVE "a loop is a live claimer RIGHT NOW" signal the
    delivery loop POSTs on becoming ready (`claimer-acquire`) and clears on
    teardown (`claimer-release`). This is the disambiguator that unblocks the
    Task 5.1b deaf-target fail-fast: unlike the channel-sidecar heartbeat (which
    a not-yet-polled healthy loop has not written yet), a lease is set the moment
    the loop is ready and cleared the moment it exits.

    True ONLY when the lease state is 'acquired' AND its last refresh is within
    CLAIMER_LEASE_STALE_SECONDS (backstop for a missed release after a crash).
    A 'released' lease ⇒ False IMMEDIATELY (no staleness wait). No lease row ⇒
    False (caller must treat absence-of-lease as 'fall back to the sidecar check',
    NOT as deaf — see `_has_recorded_claimer_lease`).
    """
    row = await _claimer_lease_row(db, agent_id)
    if not row:
        return False
    if str(row["state"] or "").strip().lower() != "acquired":
        return False
    updated = _iso_to_epoch(str(row["updated_at"] or ""))
    if not updated:
        return False
    age = datetime.now(timezone.utc).timestamp() - updated
    return age <= CLAIMER_LEASE_STALE_SECONDS


async def _has_recorded_claimer_lease(db, agent_id: str) -> bool:
    """WS5 Task 5.1: True when a lease has EVER been recorded for this agent
    (acquired OR released). Used to decide whether the lease is AUTHORITATIVE
    (so a released/stale lease ⇒ deaf) vs whether to fall back to the
    sidecar/bridge-freshness check (no lease ever ⇒ pre-existing/older loop or a
    lazy claimer that has not polled — must NOT be treated as deaf)."""
    return await _claimer_lease_row(db, agent_id) is not None


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


async def _managed_target_is_deaf(db, agent_row, *, settings: Optional[dict[str, Any]] = None) -> bool:
    """WS5 Task 5.1b (2026-06-02): True when a managed sidecar-delivery target is
    genuinely DEAF — it HAD a delivery-loop claimer that has RELEASED its lease (or
    let it go stale), so a send would queue against a worker that will never claim
    it.

    DEPRECATED as a SEND GATE (reversed 2026-06-02): this predicate NO LONGER
    rejects a `comms_send`. The operator reversed the deaf fail-fast because it lost
    messages to agents that were merely mid-restart; sends now always queue and the
    `_reap_undeliverable_queued_runs` backstop is the sole safety net. The helper is
    retained for any status/deliverability classification that wants the signal.

    Deaf == ALL of:
      - the runtime is a managed sidecar-delivery runtime (claude-code / hermes:
        its delivery loop / channel sidecar is the SOLE claimer); AND
      - session_mode is managed; AND
      - a claimer lease has been RECORDED for this agent (so the loop has run at
        least once and we know the lease is authoritative); AND
      - that lease is NOT live (released cleanly, or stale past the backstop).

    NOT deaf (so lazy-autostart-on-send keeps working):
      - a cold `available` agent that NEVER recorded a lease (spawnable: no worker
        yet but a bridge will spawn-on-claim) — there is NO lease row, so this
        returns False and the send falls through to the cold-start path; AND
      - an agent whose lease is currently ACQUIRED+fresh (a live claimer).

    The WS3.2 queued-run backstop still covers any in-flight stranded run after
    its long age window (the lazy-claim ambiguity has resolved by then).
    """
    if agent_row is None:
        return False
    runtime = _normalize_runtime(agent_row["runtime"] or "")
    if runtime not in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES:
        return False
    if _normalize_session_mode(agent_row["session_mode"] or "resident") != "managed":
        return False
    agent_id = agent_row["id"]
    # No lease EVER recorded ⇒ cold-startable, NOT deaf (preserve lazy delivery).
    if not await _has_recorded_claimer_lease(db, agent_id):
        return False
    # A lease was recorded: it is authoritative. Live ⇒ deliverable (not deaf);
    # released/stale ⇒ deaf.
    return not await _has_live_claimer_lease(db, agent_id)


async def _enforce_live_worker_gate(
    payload: dict[str, Any],
    db,
    settings: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    """Plan 5 Section C (2026-05-25): downgrade cached `online` to `available`
    for managed wrapper-backed agents that have no non-terminated
    `terminal_sessions` row.

    Why this lives at the read boundary (not in the cache):
    `_compute_live_status_cache` already consults `terminal_sessions` when it
    runs, but `agent_live_state.refresh_after` is keyed on heartbeat
    freshness via `_status_refresh_after` — NOT worker presence. When the
    wrapper PTY exits but a parallel heartbeat keeps the agent alive (e.g.
    another bridge polling the same agent), `refresh_after` stays in the
    future and `_refresh_expired_agent_live_states` never re-validates.
    Cached `status='online'` then persists indefinitely.

    Observed 2026-05-25: graph-senior-dev (codex managed) —
    `agent_live_state.status='online'` `terminal_id=''`
    `updated_at=19:29:10Z` `refresh_after=19:30:28Z` (long past), but the
    API still returned `online` because the cache row never fell behind a
    fresh-enough heartbeat to trigger a recompute.

    This gate is a final-step correction at the API boundary. Cache stays
    for performance; the writeback below keeps subsequent reads honest
    without re-running the terminal_sessions check.
    """
    if payload.get("status") not in {"online", "ready"}:
        return payload
    session_mode = str(payload.get("sessionMode") or "").lower()
    if session_mode != "managed":
        return payload
    runtime = str(payload.get("runtime") or "").lower()
    if not _managed_via_wrapper_for_runtime(settings, runtime):
        return payload
    if await _has_live_terminal_session(db, agent_id):
        return payload
    payload["status"] = "available"
    payload["statusRaw"] = "available"
    payload["statusNote"] = "no-live-worker (Plan 5 read-path gate)"
    # Task C2 — writeback so the dashboard's next poll sees the downgrade
    # without re-running the live-worker check. Best-effort: a failure
    # only means the next read re-runs the gate, which is still cheap.
    try:
        await db.execute(
            """UPDATE agent_live_state
               SET status = ?, reason = ?, updated_at = ?
               WHERE agent_id = ?""",
            (
                "available",
                "no-live-worker (Plan 5 read-path gate)",
                _now(),
                agent_id,
            ),
        )
        await db.commit()
    except Exception:
        logger.debug(
            "live-worker writeback failed for agent_id=%s; next read will re-run the gate",
            agent_id,
            exc_info=True,
        )
    return payload


def _synth_terminal_should_be_created(runtime: str, settings: dict[str, Any]) -> bool:
    """Plan 4 (2026-05-25): synth-terminal (aify://virtual-rpc/<runtime>) is
    deprecated for wrapper-backed runtimes. The wrapper PTY IS the terminal.
    Synth stays for native managed runtimes such as pi/opencode and for
    native-controller fallback when wrapper backing is disabled.
    """
    if _managed_via_wrapper_for_runtime(settings, runtime):
        return False
    return True


def _insert_messages_via_console(settings: dict[str, Any]) -> bool:
    """Universal delivery-mode toggle (operator's design).

    Returns True when managed dispatches should write the message body
    DIRECTLY into the wrapper PTY (legacy console-typed delivery) and
    False (default, target architecture) when dispatches should flow
    through each runtime's proper delivery channel: claude-channel.js
    notifications for managed claude, native RPC adapters
    (createPiController / createCodexController / opencode SDK) for
    the native managed runtimes.

    Earlier name was _claude_managed_channel_only with INVERTED polarity
    (channel-mode was opt-in). Renamed + inverted so the proper-delivery
    path is the default and the PTY-input fallback is the opt-in
    escape hatch.
    """
    return bool(settings.get("insert_messages_via_console", DEFAULT_SETTINGS["insert_messages_via_console"]))


async def _apply_channel_routing_to_claude_runs(db, runs, settings: dict[str, Any]) -> None:
    """Post-create patch: when insert_messages_via_console=false (the
    default + target architecture), force the execution_mode of
    dispatch_runs targeting sidecar-channel managed agents from 'managed'
    to 'channel' so the in-session sidecar claims them instead of the
    generic managed worker. Idempotent; skips when via-console mode
    is enabled (in which case PTY-input delivery handles managed claude).

    Runtime-generic (Task 1.5, 2026-05-30): patches managed claude-code
    UNCONDITIONALLY and managed hermes ONLY when its channel-enabled flag
    (runtime_config.channelEnabled, set by the hermes-aify wrapper via
    AIFY_CHANNELS_ENABLED=1) is present — mirroring _channel_managed_eligible.
    claude-channel.js / hermes-channel.js claim the resulting channel runs.
    The function name is kept for call-site stability."""
    if _insert_messages_via_console(settings):
        return
    run_ids = [str(run.get("runId") or "") for run in (runs or []) if run and run.get("runId")]
    if not run_ids:
        return
    placeholders = ",".join("?" for _ in run_ids)
    # Unconditional channel-managed runtimes (claude-code).
    unconditional = sorted(_CHANNEL_MANAGED_RUNTIMES)
    if unconditional:
        rt_placeholders = ",".join("?" for _ in unconditional)
        await db.execute(
            f"""
            UPDATE dispatch_runs
            SET execution_mode = 'channel'
            WHERE id IN ({placeholders})
              AND execution_mode != 'channel'
              AND target_agent IN (
                SELECT id FROM agents
                WHERE LOWER(COALESCE(runtime, '')) IN ({rt_placeholders})
                  AND session_mode = 'managed'
              )
            """,
            [*run_ids, *unconditional],
        )
    # Flag-gated channel-managed runtimes (hermes): only when the wrapper set
    # runtime_config.channelEnabled. json_extract on the agents.runtime_config
    # column resolves the flag inline; truthy values ('true'/'1'/1) all qualify.
    flag_gated = sorted(_CHANNEL_FLAG_GATED_RUNTIMES)
    if flag_gated:
        rt_placeholders = ",".join("?" for _ in flag_gated)
        await db.execute(
            f"""
            UPDATE dispatch_runs
            SET execution_mode = 'channel'
            WHERE id IN ({placeholders})
              AND execution_mode != 'channel'
              AND target_agent IN (
                SELECT id FROM agents
                WHERE LOWER(COALESCE(runtime, '')) IN ({rt_placeholders})
                  AND session_mode = 'managed'
                  AND LOWER(COALESCE(
                        CASE
                          WHEN json_valid(runtime_config)
                          THEN json_extract(runtime_config, '$.channelEnabled')
                          ELSE NULL
                        END, ''
                      )) IN ('true', '1')
              )
            """,
            [*run_ids, *flag_gated],
        )
        # Visible-TUI managed model (2026-05-31): the channelEnabled flag is set
        # by an in-session wrapper MCP's auto-register. In the visible-TUI model
        # the managed agent's runtime IS the thin `hermes --tui` (a WS client),
        # so that flag is never set on an already-managed agent — but a LIVE
        # standalone channel-sidecar (the hermes-managed-host.js delivery loop)
        # is heartbeating and IS the authoritative channel mechanism. Route to
        # 'channel' whenever such a live sidecar exists, regardless of the flag.
        # (Observed on gov-tui 2026-05-30: a queued run stayed execution_mode=
        # 'managed' because runtime_config.channelEnabled was None, so the loop —
        # which claims only channel/resident — never matched it.) This is the
        # robust route: it reflects the live delivery reality, not a flag the
        # visible-TUI model structurally cannot set.
        sidecar_run_rows = await (
            await db.execute(
                f"""
                SELECT dr.id AS run_id, dr.target_agent AS target_agent
                FROM dispatch_runs dr
                JOIN agents a ON a.id = dr.target_agent
                WHERE dr.id IN ({placeholders})
                  AND dr.execution_mode != 'channel'
                  AND LOWER(COALESCE(a.runtime, '')) IN ({rt_placeholders})
                  AND a.session_mode = 'managed'
                """,
                [*run_ids, *flag_gated],
            )
        ).fetchall()
        live_sidecar_run_ids: list[str] = []
        live_sidecar_cache: dict[str, bool] = {}
        for row in sidecar_run_rows:
            target = str(row["target_agent"] or "")
            if target not in live_sidecar_cache:
                live_sidecar_cache[target] = await _has_live_channel_sidecar(db, target)
            if live_sidecar_cache[target]:
                live_sidecar_run_ids.append(str(row["run_id"]))
        if live_sidecar_run_ids:
            ls_placeholders = ",".join("?" for _ in live_sidecar_run_ids)
            await db.execute(
                f"""
                UPDATE dispatch_runs
                SET execution_mode = 'channel'
                WHERE id IN ({ls_placeholders})
                  AND execution_mode != 'channel'
                """,
                live_sidecar_run_ids,
            )


async def _reroute_orphaned_managed_channel_runs(db, *, limit: int = 200) -> int:
    """Reconcile (2026-06-03): a managed_via_wrapper agent's QUEUED run can be
    stuck at execution_mode='managed' when it was created BEFORE the agent's
    channel-sidecar/flag came up — the SPAWN-INITIAL message is the common case
    (the spawn creates the run, THEN the agent registers; _apply_channel_routing
    runs only at create-time and never re-runs for that run). The generic managed
    worker never claims it — managed claude/hermes delivery is owned by the
    channel-sidecar loop, which claims only channel/resident — so it sits queued
    forever (the live test confirmed: a fresh send routed to 'channel' and the
    agent replied 'ALIVE', but the spawn-initial run stayed 'managed' + unclaimed).
    This re-applies channel routing to ANY queued 'managed' run whose target now
    has a LIVE channel-sidecar (the authoritative delivery mechanism). Idempotent;
    skips when insert_messages_via_console is enabled. Returns rows re-routed."""
    settings = await _load_settings(db)
    if _insert_messages_via_console(settings):
        return 0
    channel_runtimes = sorted(_CHANNEL_MANAGED_RUNTIMES | _CHANNEL_FLAG_GATED_RUNTIMES)
    if not channel_runtimes:
        return 0
    rt_ph = ",".join("?" for _ in channel_runtimes)
    rows = await (
        await db.execute(
            f"""
            SELECT dr.id AS run_id, dr.target_agent AS target_agent
            FROM dispatch_runs dr
            JOIN agents a ON a.id = dr.target_agent
            WHERE dr.status = 'queued'
              AND dr.execution_mode = 'managed'
              AND LOWER(COALESCE(a.runtime, '')) IN ({rt_ph})
              AND a.session_mode = 'managed'
            LIMIT ?
            """,
            [*channel_runtimes, limit],
        )
    ).fetchall()
    reroute_ids: list[str] = []
    sidecar_cache: dict[str, bool] = {}
    for row in rows:
        target = str(row["target_agent"] or "")
        if target not in sidecar_cache:
            sidecar_cache[target] = await _has_live_channel_sidecar(db, target)
        if sidecar_cache[target]:
            reroute_ids.append(str(row["run_id"]))
    if not reroute_ids:
        return 0
    ph = ",".join("?" for _ in reroute_ids)
    await db.execute(
        f"UPDATE dispatch_runs SET execution_mode = 'channel' "
        f"WHERE id IN ({ph}) AND execution_mode != 'channel'",
        reroute_ids,
    )
    await db.commit()
    return len(reroute_ids)


_SPAWN_MODES = {"managed-warm"}
ACTIVE_RUN_BRIDGE_STALE_SECONDS = 120
CLAUDE_RESIDENT_DELIVERY_SUMMARY_PREFIX = "Delivered to Claude resident session"
CLAUDE_CHANNEL_DELIVERY_SUMMARY_PREFIX = "Delivered to Claude channel session"

async def _get_ws(request: Request):
    try:
        return request.app.state.ws_manager
    except Exception:
        return None


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
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": cache.get("status") or "",
            "statusNote": cache.get("reason") or "",
        })
    except Exception:
        pass

async def _touch_agent(db, agent_id: str):
    await db.execute(
        "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?",
        (_now(), agent_id)
    )


def _json_loads_or(value: Any, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _timestamp_sort_key(value: Any) -> str:
    try:
        raw = str(value or "").strip()
        if not raw:
            return ""
        from datetime import datetime, timezone
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return str(value or "")


def _bridge_started_at(metadata: Any) -> str:
    if isinstance(metadata, dict):
        return _timestamp_sort_key(metadata.get("bridgeStartedAt"))
    return ""


def _normalize_session_mode(mode: Any) -> str:
    value = str(mode or "resident").strip().lower()
    return value if value in _SESSION_MODES else "resident"


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
        return adapter_for(normalized).resume_command(handle) or ""
    except Exception:
        return ""


def _normalize_runtime(runtime: Any) -> str:
    key = str(runtime or "generic").strip().lower()
    return _RUNTIME_ALIASES.get(key, key or "generic")


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


_SHELL_PLACEHOLDER_HANDLE_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")


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


def _runtime_state_replacing_handle(runtime: Any, runtime_state: Any, session_handle: str) -> dict[str, Any]:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    result = dict(state or {})
    result.pop("sessionId", None)
    result.pop("threadId", None)
    return _runtime_state_with_handle(runtime, result, session_handle)


def _session_capabilities_replacing_handle(capabilities: Any, session_handle: str) -> dict[str, Any]:
    existing = capabilities if isinstance(capabilities, dict) else _json_loads_or(capabilities, {})
    result = dict(existing or {}) if isinstance(existing, dict) else {}
    handle_present = bool(str(session_handle or "").strip())
    result.setdefault("persistent", True)
    result["bridgeResume"] = True
    result["nativeResume"] = handle_present
    return result


def _normalize_machine_id(machine_id: Any) -> str:
    """Canonical machine_id form for storage AND comparison.

    The host machine_id is "<platform>:<hostname>" (e.g. "win32:StevenZ-L").
    Different launch paths report the hostname with different casing, and
    machine_id is compared in bridge supersession + dispatch-claim routing.
    Comparing case-sensitively let a re-registered worker under a different
    casing escape supersession, leaving duplicate live bridge_instances.
    Lowercasing is safe (platform is already lowercase, only host casing
    varies) and idempotent, so we normalize at every store/compare site.
    """
    return str(machine_id or "").strip().lower()


def _machine_family(machine_id: Any) -> str:
    return str(machine_id or "").strip().split(":", 1)[0].lower()


def _machine_ids_same_host(a: Any, b: Any) -> bool:
    """Tolerant machine_id equality for dispatch-claim routing.

    machine_id is "<platform>:<host>". On WSL the platform tag is unstable
    across spawn contexts: the SAME machine registers as both
    "wsl-<distro>:host" (when WSL_DISTRO_NAME is set) and "linux:host" (when it
    isn't), because the env var is not propagated to every process. An exact
    comparison then treats one machine as two — a WSL delivery loop
    ("wsl-ubuntu:host") could never claim runs for a WSL-registered agent
    ("linux:host"), so deliveries sat queued forever (observed 2026-06-02,
    ci-senior-dev). Collapse the linux/WSL platform family so they match, while
    keeping win32/darwin distinct (a Windows bridge must NOT claim a WSL agent's
    runs). Fully generic: only the host component and a family collapse are
    compared, nothing machine-specific.
    """
    na, nb = _normalize_machine_id(a), _normalize_machine_id(b)
    if na == nb:
        return True
    if not na or not nb:
        return False
    fa, _, ha = na.partition(":")
    fb, _, hb = nb.partition(":")
    if not ha or ha != hb:
        return False

    def _fam(f: str) -> str:
        return "linux" if f == "linux" or f.startswith("wsl") else f

    return _fam(fa) == _fam(fb)


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dispatch_requires_reply(explicit: Optional[bool], *, default: bool) -> bool:
    if explicit is None:
        return bool(default)
    return bool(explicit)


def _message_type_expects_reply(message_type: str) -> bool:
    return (message_type or "").strip().lower() in {"request", "review", "error"}


def _row_require_reply(row) -> bool:
    return bool(int((row["require_reply"] if row and "require_reply" in row.keys() else 0) or 0))


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


def _dispatch_reply_pending(row) -> bool:
    return _dispatch_reply_state(row) == "pending"


def _is_operator_closed_contract(row) -> bool:
    if not row:
        return False
    status = str((row["status"] if "status" in row.keys() else "") or "").strip().lower()
    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    return (
        status == "completed"
        and not _row_require_reply(row)
        and summary.startswith("Closed from Work Loop by dashboard operator.")
    )


def _contract_reply_expected(row) -> bool:
    if not row:
        return False
    if _is_operator_closed_contract(row):
        return False
    if _row_require_reply(row):
        return True
    message_type = str((row["message_type"] if "message_type" in row.keys() else "") or "").strip().lower()
    if message_type in {"info", "response", "approval"}:
        return False
    priority = str((row["priority"] if "priority" in row.keys() else "") or "").strip().lower()
    return message_type in {"request", "review", "error"} or priority in {"high", "urgent"}


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


def _contract_row_to_dict(row, *, settings: dict[str, Any], now_s: Optional[float] = None) -> dict[str, Any]:
    state = _contract_state(row, settings=settings, now_s=now_s)
    body = str((row["message_body"] if row and "message_body" in row.keys() else "") or row["body"] or "")
    result_body = str((row["result_body"] if row and "result_body" in row.keys() else "") or "")
    result_message_id = str(row["result_message_id"] or "").strip()
    reply_state = _dispatch_reply_state(row)
    if state["replyExpected"] and reply_state == "not_required":
        reply_state = "sent" if result_message_id else "awaiting"
    return {
        "id": row["id"],
        "messageId": row["message_id"] or "",
        "from": row["from_agent"],
        "targetAgentId": row["target_agent"],
        "type": row["message_type"],
        "subject": row["subject"] or "",
        "preview": body[:420],
        "priority": row["priority"] or "normal",
        "status": row["status"],
        "runtime": row["runtime"] or "",
        "requireReply": _row_require_reply(row),
        "replyState": reply_state,
        "resultMessageId": result_message_id,
        "resultPreview": result_body[:420],
        "requestedAt": row["requested_at"],
        "claimedAt": row["claimed_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "sourceReadAt": row["source_read_at"] or "",
        "lastReminderAt": row["last_reminder_at"] or "",
        **state,
    }


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


def _has_codex_live_app_server(runtime_config: Optional[dict[str, Any]] = None) -> bool:
    if not isinstance(runtime_config, dict):
        return False
    return str(runtime_config.get("appServerUrl") or "").strip().lower().startswith(("ws://", "wss://"))


def _has_hermes_gateway_url(runtime_config: Optional[dict[str, Any]] = None) -> bool:
    """Plan 4 Task 17: hermes resident uses the gateway path when a live
    gatewayUrl is present in runtime_config. Mirrors the bridge-side check
    in mcp/stdio/server.js."""
    if not isinstance(runtime_config, dict):
        return False
    return str(runtime_config.get("gatewayUrl") or "").strip().lower().startswith(("ws://", "wss://"))


def _normalize_channel_history_where(channel_name: str) -> tuple[str, tuple[Any, ...]]:
    return "channel = ? AND to_agent IS NULL", (channel_name,)


def _channel_fanout_message_id(canonical_message_id: str, agent_id: str) -> str:
    return f"{canonical_message_id}-{agent_id}"


def _validate_registration_cwd(
    *,
    agent_id: str,
    runtime: str,
    session_mode: str,
    machine_id: str,
    cwd: str,
    runtime_config: Optional[dict[str, Any]] = None,
) -> None:
    normalized_runtime = _normalize_runtime(runtime)
    normalized_session_mode = _normalize_session_mode(session_mode)
    resolved_cwd = str(cwd or "").strip()
    family = _machine_family(machine_id)
    if not resolved_cwd or normalized_runtime != "codex" or normalized_session_mode != "resident":
        return
    if not _has_codex_live_app_server(runtime_config):
        return
    if family in {"linux", "darwin", "wsl"} and _WINDOWS_DRIVE_CWD_RE.match(resolved_cwd):
        hint = '/mnt/<drive>/...' if family in {"linux", "wsl"} else "/Users/..."
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on {family}. '
                f'Use a native host path such as "{hint}", not a Windows drive-letter path.'
            ),
        )
    if family == "win32" and _WSL_DRIVE_CWD_RE.match(resolved_cwd):
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on Windows. '
                'Use forward-slash drive-letter form like "C:/repo", not a "/mnt/..." WSL path.'
            ),
        )


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
        await db.execute(f"UPDATE dispatch_controls SET source_message_id = '' WHERE source_message_id IN ({placeholders})", chunk)
        await db.execute(f"DELETE FROM read_receipts WHERE message_id IN ({placeholders})", chunk)
        cursor = await db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", chunk)
        deleted += cursor.rowcount or 0
    return deleted


async def _cancel_queued_dispatch_runs_for_message_ids(db, message_ids: list[str], *, chunk_size: int = 250) -> list[str]:
    pending = _dedupe_preserve([str(message_id or "").strip() for message_id in message_ids if str(message_id or "").strip()])
    if not pending:
        return []

    cancelled_ids = []
    finished_at = _now()
    summary = "Cancelled because source message was unsent."
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor = await db.execute(
            f"SELECT id FROM dispatch_runs WHERE status = 'queued' AND message_id IN ({placeholders})",
            chunk,
        )
        run_ids = [str(row["id"]) for row in await cursor.fetchall()]
        if not run_ids:
            continue
        run_placeholders = ",".join("?" for _ in run_ids)
        await db.execute(
            f"UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id IN ({run_placeholders})",
            (summary, finished_at, *run_ids),
        )
        for run_id in run_ids:
            await _append_dispatch_event(db, run_id, "cancelled", summary)
        cancelled_ids.extend(run_ids)
    return cancelled_ids


async def _delete_messages_where(db, where_clause: str, params: tuple[Any, ...] = ()) -> int:
    message_ids = await _select_message_ids(db, where_clause, params)
    return await _delete_messages_by_ids(db, message_ids)


async def _agent_tombstone(db, agent_id: str):
    cursor = await db.execute("SELECT * FROM agent_tombstones WHERE agent_id = ?", (agent_id,))
    return await cursor.fetchone()


async def _tombstone_agent(
    db,
    agent_id: str,
    *,
    removed_by: str = "",
    bridge_id: str = "",
    reason: str = "",
    removed_at: Optional[str] = None,
):
    await db.execute(
        """
        INSERT OR REPLACE INTO agent_tombstones (
            agent_id, removed_at, removed_by, bridge_id, reason
        ) VALUES (?,?,?,?,?)
        """,
        (agent_id, removed_at or _now(), removed_by, bridge_id, reason),
    )


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
    cursor = await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    return cursor.rowcount or 0


def _default_capabilities_for(
    runtime: str,
    session_mode: str,
    session_handle: str = "",
    runtime_config: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Build the default capability list for an agent registration.

    Plan 3 (2026-05-25): resident gating routes through adapter.is_resident_ready()
    which closes the #120 regression — claude resident needs channelEnabled,
    hermes resident needs a valid gatewayUrl, both rolled into the adapter.
    """
    from service.runtimes import adapter_for

    runtime_n = _normalize_runtime(runtime or "")
    try:
        adapter = adapter_for(runtime_n)
    except ValueError:
        return []

    caps: list[str] = []
    session_mode_n = _normalize_session_mode(session_mode or "")

    if session_mode_n == "resident":
        # Plan 3: adapter.is_resident_ready() encapsulates per-runtime,
        # per-config gating (channelEnabled for claude, gatewayUrl for hermes).
        if adapter.supports_resident and adapter.is_resident_ready(runtime_config or {}):
            caps.append("resident-run")
    else:
        if adapter.supports_managed:
            caps.append("managed-run")

    if adapter.supports_resident or adapter.supports_managed:
        caps.append("resume")
    if adapter.supports_interrupt:
        caps.append("interrupt")
    if adapter.supports_steering:
        caps.append("steer")

    # `spawn` capability is independent — every aify-comms managed-capable
    # runtime supports being spawned by another agent's environment.
    if session_mode_n != "resident" and adapter.supports_managed:
        caps.append("spawn")

    return caps


async def _resolve_recipient_ids(db, *, to: Optional[str], to_role: Optional[str], from_agent: str) -> list[str]:
    recipients: list[str] = []
    if to:
        recipients.append(to)
    if to_role:
        cursor = await db.execute("SELECT id FROM agents WHERE role = ? AND id != ?", (to_role, from_agent))
        recipients.extend([row["id"] for row in await cursor.fetchall()])
    return _dedupe_preserve(recipients)


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
            for cap in ("managed-run", "resume", "interrupt", "spawn"):
                if cap not in capabilities:
                    capabilities = [*capabilities, cap]
        elif _has_hermes_gateway_url(runtime_config):
            for cap in ("resident-run", "resume", "interrupt"):
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

    if session_mode == "resident" and "resident bridge is stale" in reason:
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


def _format_dispatch_state(active_row, queued_count: int) -> dict[str, Any]:
    active = None
    if active_row:
        active = {
            "runId": active_row["id"],
            "status": active_row["status"],
            "subject": active_row["subject"],
            "from": active_row["from_agent"],
            "dispatchMode": active_row["dispatch_mode"] or "",
            "executionMode": active_row["execution_mode"] or "managed",
            "runtime": active_row["runtime"] or "",
            "claimBridgeId": active_row["claim_bridge_id"] or "",
            "requestedAt": active_row["requested_at"] or "",
            "startedAt": active_row["started_at"] or active_row["claimed_at"] or "",
        }
    return {
        "hasActiveRun": bool(active),
        "activeRun": active,
        "queuedRuns": max(int(queued_count or 0), 0),
    }


async def _get_dispatch_state_for_agent(db, agent_id: str) -> dict[str, Any]:
    active_cursor = await db.execute(
        """
        SELECT id, from_agent, subject, status, dispatch_mode, execution_mode, runtime, requested_at, claimed_at, started_at
             , claim_bridge_id
        FROM dispatch_runs
        WHERE target_agent = ? AND status IN ('claimed', 'running')
        ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
        LIMIT 1
        """,
        (agent_id,)
    )
    active_row = await active_cursor.fetchone()
    queued_cursor = await db.execute(
        "SELECT COUNT(*) FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
        (agent_id,)
    )
    queued_count = (await queued_cursor.fetchone())[0]
    return _format_dispatch_state(active_row, queued_count)


async def _get_dispatch_state_map(db, agent_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not agent_ids:
        return {}
    placeholders = ",".join("?" for _ in agent_ids)
    active_cursor = await db.execute(
        f"""
        SELECT id, target_agent, from_agent, subject, status, dispatch_mode, execution_mode, runtime, requested_at, claimed_at, started_at, claim_bridge_id
        FROM dispatch_runs
        WHERE target_agent IN ({placeholders}) AND status IN ('claimed', 'running')
        ORDER BY target_agent ASC, COALESCE(started_at, claimed_at, requested_at) ASC
        """,
        tuple(agent_ids),
    )
    active_rows = await active_cursor.fetchall()
    queued_cursor = await db.execute(
        f"SELECT target_agent, COUNT(*) AS queued_count FROM dispatch_runs WHERE target_agent IN ({placeholders}) AND status = 'queued' GROUP BY target_agent",
        tuple(agent_ids),
    )
    queued_rows = await queued_cursor.fetchall()
    queued_counts = {row["target_agent"]: int(row["queued_count"] or 0) for row in queued_rows}
    active_by_agent: dict[str, Any] = {}
    for row in active_rows:
        active_by_agent.setdefault(row["target_agent"], row)
    return {
        agent_id: _format_dispatch_state(active_by_agent.get(agent_id), queued_counts.get(agent_id, 0))
        for agent_id in agent_ids
    }


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


async def _resident_bridge_is_fresh(db, row, *, lease_seconds: int) -> bool:
    lease = max(15, int(lease_seconds or 150))
    bridge_id = str(_json_loads_or(row["runtime_state"], {}).get("bridgeInstanceId") or "").strip()
    if bridge_id:
        cursor = await db.execute(
            "SELECT last_seen, superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
            (bridge_id, row["id"]),
        )
        bridge = await cursor.fetchone()
        if bridge and not str((bridge["superseded_by"] if "superseded_by" in bridge.keys() else "") or "").strip():
            seen_s = _iso_to_epoch((bridge["last_seen"] or ""))
            if seen_s and time.time() - seen_s <= lease:
                return True
    # Fallback (operator-reported 2026-05-31, sc-manager): an IDLE resident
    # claude's MCP bridge is NOT heartbeated — the turn-busy heartbeat only fires
    # during an active turn, and the session-handle heartbeat only POSTs when the
    # session id CHANGES. So a live-but-idle resident goes stale after the lease
    # and the dashboard shows it dead. Its channel sidecar (claude-channel.js) is
    # a CHILD of the live session and polls /dispatch/claim every ~3s, so a fresh,
    # non-superseded channel-sidecar bridge is proof the resident session is
    # alive. Treat that as fresh too. (If the session dies, the sidecar child dies
    # and its bridge goes stale — so this never masks a genuinely dead resident.)
    # KEPT (Task A' #154, 2026-06-01): still needed even with the 30s liveness
    # beat. Residents are operator-launched and may run a MIXED bridge version
    # that predates liveness-heartbeat.js, so the resident MCP bridge can still
    # go stale while idle; a live channel sidecar is the proof-of-life fallback.
    # Removal probe broke test_idle_resident_with_live_sidecar_is_not_stale.
    if await _has_live_channel_sidecar(db, row["id"]):
        return True
    return False


async def _turn_busy_state(db, agent_id: str) -> tuple[bool, str]:
    """Return (fresh, turn_run_id) for the agent's agent_turn_state row.

    `fresh` is True when turn_busy=1 was updated within TURN_BUSY_STALE_SECONDS.
    `turn_run_id` is the run the bridge attributed that turn-busy pulse to (''
    when unknown). Callers that only need the boolean use _is_turn_busy_fresh;
    the reminder loop needs the run id so it can tell a GENUINE other-work
    turn_busy apart from a delivered-run's OWN delivery re-pulse (which would
    otherwise make a handoff skip its own reminder forever — deadlock)."""
    try:
        row = await (await db.execute(
            "SELECT turn_busy, turn_run_id, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()
    except Exception:
        return (False, "")
    if not row or not int((row["turn_busy"] if "turn_busy" in row.keys() else 0) or 0):
        return (False, "")
    seen = _iso_to_epoch(str(row["turn_updated_at"] or ""))
    fresh = bool(seen and time.time() - seen <= TURN_BUSY_STALE_SECONDS)
    run_id = str((row["turn_run_id"] if "turn_run_id" in row.keys() else "") or "")
    return (fresh, run_id)


async def _is_turn_busy_fresh(db, agent_id: str) -> bool:
    """True when the agent is mid-turn per agent_turn_state — turn_busy=1 updated
    within TURN_BUSY_STALE_SECONDS. This is the canonical 'busy via turn' half of
    the shared busy definition: an agent is BUSY iff it has a claimed/running
    dispatch run (hasActiveRun) OR a fresh turn_busy. The status engine
    (_compute_live_status_cache) and the dispatch claim-gate already use it; this
    helper lets the reminder loop use the SAME definition instead of hasActiveRun
    alone, so a mid-turn agent with no tracked run (e.g. a resident claude on its
    own turn) is not reminder-nagged while working. (holistic status review
    Finding 2, 2026-05-31.)"""
    fresh, _run_id = await _turn_busy_state(db, agent_id)
    return fresh


async def _fresh_same_mode_bridge_conflict(
    db,
    *,
    agent_id: str,
    machine_id: str,
    new_bridge_id: str,
    session_mode: str,
    lease_seconds: int,
):
    """Return a LIVE same-mode bridge that a new registration would race.

    Phase 4 race guard (2026-05-31, operator-chosen hard-error model). A fresh,
    non-superseded bridge for the SAME (agent, machine) and the SAME resident
    session_mode, owned by a DIFFERENT bridge_id, means a second live wrapper is
    about to claim an identity already being driven — silently superseding it
    would kill the first wrapper's work. We surface that as a 409 (unless the
    caller passes force=true to take over deliberately).

    Scope is RESIDENT-only: managed bridges intentionally use latest-launch-wins
    to reap zombie wrappers, and the visible-TUI managed model runs a legitimate
    sidecar + wrapper-child pair concurrently — neither should trip this guard.
    Returns the conflicting bridge row, or None when there is no live conflict.
    """
    if _normalize_session_mode(session_mode or "") != "resident":
        return None
    normalized_machine = _normalize_machine_id(machine_id)
    cutoff = max(15, int(lease_seconds or 150))
    cursor = await db.execute(
        """
        SELECT id, last_seen, bridge_kind
        FROM bridge_instances
        WHERE agent_id = ?
          AND machine_id = ?
          AND id != ?
          AND session_mode = 'resident'
          AND COALESCE(superseded_by, '') = ''
        ORDER BY last_seen DESC
        """,
        (agent_id, normalized_machine, str(new_bridge_id or "").strip()),
    )
    for bridge in await cursor.fetchall():
        seen_s = _iso_to_epoch((bridge["last_seen"] or ""))
        if seen_s and (time.time() - seen_s) <= cutoff:
            return bridge
    return None


async def _session_handle_live_owner(db, handle: str, *, exclude_agent_id: str, lease_seconds: int):
    """Return a DIFFERENT, currently-LIVE agent that already owns `handle`.

    Cross-agent session-id collision guard (root cause of the 2026-05-31
    incident): a runtime session id must be owned by at most ONE live agent.
    When graph-tech-lead (a managed launch) adopted comms-tech-lead's live
    resident session id 651b895f, the kill-prior reaper then turned that
    collision fatal. This detects the collision at the source — before a handle
    is adopted — so it can be parked instead of bound.

    "Live" = another agent with the same session_handle whose heartbeat is fresh
    within the resident lease (a dead/stale owner means the id is effectively
    free to reassign, so it is NOT a collision). Returns {agentId, sessionMode}
    of the live owner, or None.
    """
    h = str(handle or "").strip()
    if not h:
        return None
    cutoff = max(60, int(lease_seconds or 150))
    cursor = await db.execute(
        "SELECT id, last_seen, session_mode FROM agents WHERE session_handle = ? AND id != ?",
        (h, str(exclude_agent_id or "").strip()),
    )
    for r in await cursor.fetchall():
        seen = _iso_to_epoch(r["last_seen"] or "")
        if seen and (time.time() - seen) <= cutoff:
            return {"agentId": r["id"], "sessionMode": str(r["session_mode"] or "")}
    return None


async def _latest_spawn_spec(db, agent_id: str):
    return await (await db.execute(
        "SELECT * FROM spawn_specs WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
        (agent_id,),
    )).fetchone()


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


async def _apply_pending_resident_takeover_if_ready(db, agent_id: str) -> bool:
    # Manual ownership model: a resident CLI registration must not take over a
    # managed identity at a turn boundary. Operators use /session-mode.
    return False


async def _bridge_is_superseded(db, bridge_id: str, agent_id: str) -> bool:
    if not bridge_id:
        return False
    cursor = await db.execute(
        "SELECT superseded_by, bridge_kind FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id)
    )
    row = await cursor.fetchone()
    if not row:
        return False
    return bool((row["superseded_by"] or "").strip())


async def _active_wrapper_terminal_id(db, agent_id: str, *, settings: dict[str, Any]) -> str:
    terminal = await _active_terminal_for_agent(db, agent_id, settings=settings)
    if not terminal:
        return ""
    try:
        return str(terminal["terminal_id"] or terminal["id"] or "").strip()
    except Exception:
        return str((terminal.get("terminal_id") or terminal.get("id") or "") if isinstance(terminal, dict) else "").strip()


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def _terminal_text_compact(text: str) -> str:
    cleaned = _ANSI_RE.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


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


async def _bridge_registered_at(db, bridge_id: str, agent_id: str) -> str:
    if not bridge_id:
        return ""
    cursor = await db.execute(
        "SELECT registered_at FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id)
    )
    row = await cursor.fetchone()
    if not row:
        return ""
    return row["registered_at"] or ""


async def _reconcile_stale_managed_terminals_for_resident_agents(db) -> int:
    """Service-start event-driven cleanup.

    When the service container restarts, any in-flight managed wrapper
    PTYs are dead (their bridge process died with the previous service).
    For agents that are currently registered as resident (operator's
    *-aify wrapper owns the terminal), the existing managed PTY rows
    must NOT be displayed as live consoles — the dashboard would show
    ghosts and users get confused.

    This sweep fires once at service startup (an event, not a timer).
    For each resident agent, mark any terminal_sessions in active
    states as stopped and clear the agent_sessions.terminal_id binding
    so the dashboard renders the resident-owned state cleanly.

    Returns the number of terminal_sessions that were reconciled.
    """
    cursor = await db.execute(
        """
        SELECT t.id AS terminal_id, t.agent_id
        FROM terminal_sessions t
        JOIN agents a ON a.id = t.agent_id
        WHERE a.session_mode = 'resident'
          AND t.status IN ('starting','attached','running','active','idle','recovering')
        """
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    now = _now()
    for row in rows:
        terminal_id = row["terminal_id"]
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                stopped_at = ?,
                updated_at = ?,
                error = COALESCE(NULLIF(error, ''), 'reconciled_at_service_startup_resident_owns_agent')
            WHERE id = ?
            """,
            (now, now, terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "reconciled_at_service_startup",
            json.dumps({
                "agentId": row["agent_id"],
                "reason": "agent is registered as resident; bridge-spawned managed PTY rows from before service-restart are dead",
            }),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = '',
                terminal_status = ''
            WHERE terminal_id = ?
            """,
            (terminal_id,),
        )
    return len(rows)


async def _reconcile_managed_worker_hygiene(db) -> dict[str, int]:
    """Periodic managed-worker hygiene sweep (Workstream B).

    Scoped to MANAGED claude-code agents — the surface where the incident
    occurs. claude-aify's claude-channel.js sidecar beats every 30s while the
    wrapper is alive (Workstream A liveness), so `_has_live_channel_sidecar`
    is a TRUE "alive now" signal: the sidecar's bridge_instances row goes
    stale within CHANNEL_SIDECAR_STALE_SECONDS after the worker dies.

    B1 — ghost-console half (implemented here):
      A managed claude wrapper dies but its `terminal_sessions` row stays in
      an active state (`attached`, etc.), so the dashboard renders a phantom
      "Console attached" for a dead agent. We reap that ghost row ONLY when
      the worker is genuinely dead (no live channel sidecar) — a live-but-idle
      console is never falsely reaped.

    B2 — orphan-worker half (implemented here, 2026-06-01): the inverse. The
    console PTY died but the channel-sidecar keeps beating, so the agent looks
    like a LIVE worker with NO visible console = a headless background orphan
    (visible-TUI violation + proliferation). We clear the stale console pointer,
    invalidate the live-status cache (so the refined status-F1 recomputes the
    agent to `available`), append an observability event, and count it. The
    actual process kill is host-side (B3: tree-kill on PTY close). We do NOT emit
    a dispatch_control — an orphan has no run, so there is no run_id to attach
    one to. A MANAGED_ORPHAN_GRACE_SECONDS guard prevents reaping a console that
    is merely restarting between liveness beats.

    DB-only: the reconcile loop has no `ws` in scope; the dashboard reflects
    the reaped row on its next refresh (Workstream C adds WS push later).
    """
    result = {"managed_ghost_rows_reaped": 0, "orphan_workers_reaped": 0}
    cursor = await db.execute(
        """
        SELECT t.id AS terminal_id, t.agent_id AS agent_id, a.runtime AS runtime
        FROM terminal_sessions t
        JOIN agents a ON a.id = t.agent_id
        WHERE a.session_mode = 'managed'
          AND a.runtime IN ({placeholders})
          AND t.status IN ('starting','attached','running','active','idle','recovering')
          AND t.id NOT LIKE 'vterm_%'
        """.format(
            placeholders=",".join("?" for _ in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES)
        ),
        tuple(_CHANNEL_SIDECAR_DELIVERY_RUNTIMES),
    )
    rows = await cursor.fetchall()
    now = _now()
    for row in (rows or []):
        terminal_id = row["terminal_id"]
        agent_id = row["agent_id"]
        ghost_runtime = _normalize_runtime(str(row["runtime"] or "") if "runtime" in row.keys() else "") or "managed"
        sidecar_live = await _has_live_channel_sidecar(db, agent_id)
        if sidecar_live:
            # Worker alive — a live-but-idle console stays. The orphan-worker
            # half below handles "live sidecar + no live console".
            continue
        # Worker dead → this active terminal row is a ghost. Reap it.
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                stopped_at = ?,
                updated_at = ?,
                error = COALESCE(NULLIF(error, ''), 'reconciled_managed_ghost_console_dead_worker')
            WHERE id = ?
            """,
            (now, now, terminal_id),
        )
        # WS3 Task 3.4: runtime-aware reason. For hermes the dead claimer is the
        # delivery loop (hermes-managed-host.js, registered as a channel-sidecar);
        # for claude it is the claude-channel.js sidecar inside the wrapper PTY.
        if ghost_runtime == "hermes":
            ghost_reason = (
                "managed hermes delivery loop is dead (no live channel sidecar) but its "
                "console terminal row stayed active; phantom console reaped"
            )
        else:
            ghost_reason = (
                f"managed {ghost_runtime} wrapper is dead (no live channel sidecar) but its "
                "terminal row stayed active; phantom console reaped"
            )
        await _append_terminal_event(
            db,
            terminal_id,
            "reconciled_managed_ghost_console",
            json.dumps({
                "agentId": agent_id,
                "runtime": ghost_runtime,
                "reason": ghost_reason,
            }),
        )
        # Clear the agent's runtime_state.consoleTerminal pointer (pop + write
        # back), but only if it still points at this terminal.
        agent_row = await (
            await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))
        ).fetchone()
        if agent_row:
            runtime_state = _json_loads_or(agent_row["runtime_state"], {})
            console_terminal = runtime_state.get("consoleTerminal") if isinstance(runtime_state, dict) else None
            if (
                isinstance(console_terminal, dict)
                and str(console_terminal.get("terminalId") or "").strip() == str(terminal_id)
            ):
                runtime_state.pop("consoleTerminal", None)
                await db.execute(
                    "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                    (json.dumps(runtime_state), now, agent_id),
                )
        # Clear the agent_sessions terminal binding (mirror the model fn).
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = '',
                terminal_status = ''
            WHERE terminal_id = ?
            """,
            (terminal_id,),
        )
        result["managed_ghost_rows_reaped"] += 1

    # B2 — orphan-worker half (2026-06-01): the inverse failure. The console PTY
    # died but the channel-sidecar keeps beating → the agent has a LIVE worker
    # (sidecar) with NO visible console = a "headless background orphan", which
    # violates the visible-TUI hard requirement and drives proliferation. The
    # actual process kill is host-side (B3: tree-kill on PTY close); B2 is the
    # server-side status truth: clear the stale console pointer, invalidate the
    # cache (so the refined status-F1 recomputes the agent to `available`), and
    # count it for observability. We do NOT emit a dispatch_control here — an
    # orphan has no run, so there is no run_id to attach one to.
    orphan_cursor = await db.execute(
        """
        SELECT a.id AS agent_id, a.runtime AS runtime, a.runtime_state AS runtime_state
        FROM agents a
        WHERE a.session_mode = 'managed'
          AND a.runtime IN ({placeholders})
        """.format(
            placeholders=",".join("?" for _ in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES)
        ),
        tuple(_CHANNEL_SIDECAR_DELIVERY_RUNTIMES),
    )
    orphan_agents = await orphan_cursor.fetchall()
    for agent in orphan_agents:
        agent_id = agent["agent_id"]
        orphan_runtime = _normalize_runtime(str(agent["runtime"] or "") if "runtime" in agent.keys() else "") or "managed"
        # Worker alive (sidecar beating) but NO live console PTY.
        if not await _has_live_channel_sidecar(db, agent_id):
            continue
        if await _has_live_terminal_session(db, agent_id):
            continue
        # Most-recent real (non-vterm) terminal row. No row at all = never had a
        # console → skip (avoid startup-race false positives; status-F1 already
        # reports it `available`).
        last_term = await (
            await db.execute(
                """
                SELECT id, status, stopped_at, updated_at
                FROM terminal_sessions
                WHERE agent_id = ?
                  AND id NOT LIKE 'vterm_%'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (agent_id,),
            )
        ).fetchone()
        if not last_term:
            continue
        term_status = str(last_term["status"] or "").strip().lower()
        if term_status not in ("stopped", "failed"):
            # Console is in some non-live, non-terminal state (e.g. transient) —
            # let it settle rather than reaping mid-transition.
            continue
        ended_at = str(last_term["stopped_at"] or "").strip() or str(last_term["updated_at"] or "").strip()
        ended_epoch = _iso_to_epoch(ended_at)
        if ended_epoch <= 0:
            continue
        if (_iso_to_epoch(now) - ended_epoch) < MANAGED_ORPHAN_GRACE_SECONDS:
            # Within grace — a transiently-restarting console PTY, not an orphan.
            continue
        terminal_id = str(last_term["id"] or "")
        # Clear the consoleTerminal pointer ONLY if it still points at this
        # now-dead terminal (mirror the ghost-row guard).
        runtime_state = _json_loads_or(agent["runtime_state"], {})
        console_terminal = runtime_state.get("consoleTerminal") if isinstance(runtime_state, dict) else None
        if (
            isinstance(console_terminal, dict)
            and str(console_terminal.get("terminalId") or "").strip() == terminal_id
        ):
            runtime_state.pop("consoleTerminal", None)
            await db.execute(
                "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                (json.dumps(runtime_state), now, agent_id),
            )
        if orphan_runtime == "hermes":
            orphan_reason = (
                "live hermes delivery loop (channel sidecar) but no console PTY = headless "
                "orphan (visible-TUI violation); worker killed host-side"
            )
        else:
            orphan_reason = "live sidecar but no console PTY = headless orphan; worker killed host-side"
        await _append_terminal_event(
            db,
            terminal_id,
            "reconciled_managed_orphan_worker",
            json.dumps({
                "agentId": agent_id,
                "runtime": orphan_runtime,
                "reason": orphan_reason,
            }),
        )
        # Recompute status now → refined status-F1 drops the agent to `available`.
        await _invalidate_agent_live_state(db, agent_id)
        result["orphan_workers_reaped"] += 1
    return result


async def _record_channel_sidecar_heartbeat(
    db,
    *,
    bridge_id: str,
    agent_id: str,
    machine_id: str,
    runtime: str,
    now: str,
) -> None:
    """Task 1.6b (2026-05-30): upsert the standalone channel sidecar's
    bridge_instances row from its /dispatch/claim poll, so the continuous idle
    poll itself is the liveness heartbeat.

    A standalone channel sidecar (hermes-channel.js / claude-channel.js) polls
    /dispatch/claim continuously even when idle, but until it has actually
    claimed a run there is no bridge_instances row to refresh — so the plain
    `UPDATE ... SET last_seen` in claim_dispatch matched zero rows and
    `_has_live_channel_sidecar` saw nothing, flapping the agent's status to
    `available`. Inserting (or refreshing) a `bridge_kind='channel-sidecar'`
    row keyed by the sidecar's own bridge_id makes the poll a true heartbeat.

    This deliberately does NOT run the supersession/active-run-failing pass that
    `_record_bridge_registration` does — it is a lightweight idempotent liveness
    stamp, not a (re)registration, so it must never disturb other bridge rows or
    in-flight runs. The row it writes matches exactly the columns
    `_has_live_channel_sidecar` predicates on (agent_id, bridge_kind, last_seen,
    superseded_by='').
    """
    if not bridge_id:
        return
    normalized_machine = _normalize_machine_id(machine_id)
    normalized_runtime_value = str(runtime or "generic")
    # Refresh in place if the row already exists (the common case after the
    # first poll); otherwise insert a fresh, non-superseded liveness row. Keyed
    # on the PRIMARY KEY (bridge_id) so repeated polls are idempotent.
    updated = await db.execute(
        """
        UPDATE bridge_instances
        SET last_seen = ?, bridge_kind = 'channel-sidecar'
        WHERE id = ? AND agent_id = ?
        """,
        (now, bridge_id, agent_id),
    )
    if getattr(updated, "rowcount", 0):
        return
    await db.execute(
        """
        INSERT OR IGNORE INTO bridge_instances (
            id, agent_id, machine_id, runtime, session_mode, session_handle,
            terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            bridge_id,
            agent_id,
            normalized_machine,
            normalized_runtime_value,
            "managed",
            "",
            "",
            "channel-sidecar",
            now,
            now,
            "",
            None,
        ),
    )
    # If the INSERT OR IGNORE was a no-op because a row with this id already
    # existed (race / pre-existing non-sidecar row), still refresh its
    # heartbeat and kind so the liveness signal is correct.
    await db.execute(
        """
        UPDATE bridge_instances
        SET last_seen = ?, bridge_kind = 'channel-sidecar'
        WHERE id = ? AND agent_id = ?
        """,
        (now, bridge_id, agent_id),
    )


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


async def _stop_virtual_terminals_for_superseded_bridges(
    db,
    *,
    agent_id: str,
    superseded_bridge_ids: list[str],
    now: str,
) -> None:
    """Mark synthesized virtual rpc terminal_sessions stopped when the
    bridge that owned them is superseded.

    Operator-reported symptom (2026-05-22): after restarting aify-comms,
    multiple managed pi/hermes agents flipped to `online` immediately
    even though no message had been sent and the bridge had freshly
    started — its in-memory PiSession pool was empty so there was no
    actual omp process behind the terminal_session row. Stale rows
    survive bridge restarts; the worker-detection rule then trusts the
    DB and reports `online`. Cleaning them up at supersession time is
    the right correctness fix.
    """
    if not superseded_bridge_ids:
        return
    placeholders = ",".join("?" for _ in superseded_bridge_ids)
    # Defense-in-depth (code review I6, 2026-05-22): scope by agent_id
    # too. Each bridge process today has exactly one AIFY_AGENT_ID so
    # bridge_id is unique per agent, but if multi-agent bridges land
    # later this prevents cross-agent terminal slaughter.
    cursor = await db.execute(
        f"""
        SELECT id, agent_id FROM terminal_sessions
        WHERE bridge_id IN ({placeholders})
          AND agent_id = ?
          AND command IN ({",".join("?" for _ in VIRTUAL_RPC_COMMAND_SET)})
          AND status NOT IN ('stopped', 'failed')
        """,
        (*superseded_bridge_ids, agent_id, *VIRTUAL_RPC_COMMAND_SET),
    )
    rows = await cursor.fetchall()
    for row in rows:
        terminal_id = str(row["id"] or "").strip()
        owner_agent = str(row["agent_id"] or "").strip()
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                stopped_at = COALESCE(stopped_at, ?),
                updated_at = ?,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, "Superseded by bridge re-registration; in-memory worker pool empty after restart.", terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "virtual_rpc_stopped_on_bridge_supersession",
            json.dumps({"agentId": owner_agent, "supersededBridgeIds": superseded_bridge_ids}),
        )
        if owner_agent:
            # Clear the agent's virtualTerminal* pointers so dashboard
            # status correctly reports `available` until the next dispatch
            # spawns a fresh worker.
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (owner_agent,))).fetchone()
            if agent_row:
                rs = _json_loads_or(agent_row["runtime_state"], {}) or {}
                if str(rs.get("virtualTerminalId") or "").strip() == terminal_id:
                    rs.pop("virtualTerminal", None)
                    rs.pop("virtualTerminalId", None)
                    await db.execute(
                        "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                        (json.dumps(rs), now, owner_agent),
                    )
            await _invalidate_agent_live_state(db, owner_agent)


async def _fail_active_runs_for_superseded_bridges(
    db,
    *,
    agent_id: str,
    machine_id: str,
    superseding_bridge_id: str,
    finished_at: str,
    superseded_bridge_ids: Optional[list[str]] = None,
) -> list[str]:
    # Scope-narrowed: only fail runs whose claim_bridge_id is in the explicit
    # superseded-bridge list. Callers without an explicit list fall back to
    # the legacy "any bridge_id different from the new one" behavior.
    if superseded_bridge_ids is not None:
        if not superseded_bridge_ids:
            return []
        placeholders = ",".join("?" for _ in superseded_bridge_ids)
        cursor = await db.execute(
            f"""
            SELECT id, claim_bridge_id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND status IN ('claimed', 'running')
              AND claim_machine_id = ?
              AND claim_bridge_id IN ({placeholders})
            """,
            (agent_id, machine_id, *superseded_bridge_ids),
        )
    else:
        cursor = await db.execute(
            """
            SELECT id, claim_bridge_id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND status IN ('claimed', 'running')
              AND claim_machine_id = ?
              AND COALESCE(claim_bridge_id, '') != ?
            """,
            (agent_id, machine_id, superseding_bridge_id),
        )
    rows = await cursor.fetchall()
    if not rows:
        return []

    affected_run_ids: list[str] = []
    for row in rows:
        affected_run_ids.append(row["id"])
        previous_bridge_id = (row["claim_bridge_id"] or "").strip()
        owner_label = previous_bridge_id or "legacy-unowned"
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed', error_text = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                f'Run was owned by superseded bridge instance "{owner_label}" and was replaced by "{superseding_bridge_id}" during re-registration',
                finished_at,
                row["id"],
            ),
        )
        await _append_dispatch_event(
            db,
            row["id"],
            "failed",
            f"Register supersession: {owner_label} -> {superseding_bridge_id}",
        )
    return affected_run_ids


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
    if not dispatch_state:
        return status
    if dispatch_state.get("hasActiveRun") and status not in _MANUAL_STATUSES and status not in {"stale", "offline", "blocked"}:
        return "working"
    return status


def _agent_record_to_dict(row, status: str, unread: int, dispatch_state: Optional[dict[str, Any]] = None):
    runtime = _normalize_runtime(row["runtime"] or "generic")
    session_mode = _normalize_session_mode(row["session_mode"] or "resident")
    status_note = str((row["live_reason"] if "live_reason" in row.keys() else "") or _row_status_note(row) or "").strip()
    base_status = str((row["live_status"] if "live_status" in row.keys() else "") or status or row["status"] or "idle").strip()
    # `ready` is an internal bridge/controller readiness bit. Keep it out of
    # the public agent taxonomy so operators see one idle-live state: online.
    if base_status.lower() == "ready":
        base_status = "online"
    effective_status = _status_with_dispatch(base_status, dispatch_state)
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
        "favorited": bool(int((row["favorited"] if "favorited" in row.keys() else 0) or 0)),
        # Dashboard rendering hint: resident sessions live in an
        # operator-launched terminal outside aify's PTY tracking — the
        # dashboard's "Start Console" button can't open or attach to
        # them, so the dashboard should hide the button for these.
        # Managed sessions have either a real wrapper PTY OR a
        # synthesized virtual rpc terminal — Console attaches to either.
        "consoleAvailable": session_mode != "resident",
    }


def _environment_effective_status(row, *, offline_seconds: int = 90) -> str:
    status = str(row["status"] or "online")
    if status == "online":
        try:
            last = datetime.fromisoformat(str(row["last_seen"] or "").replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last > timedelta(seconds=max(15, int(offline_seconds or 90))):
                status = "offline"
        except Exception:
            pass
    return status


async def _managed_owning_environment_row(db, agent_row, *, resolved_environment_id: str = ""):
    """FIX B (2026-06-02): resolve the OWNING environment row for a MANAGED agent.

    A managed agent can only be spawned/hosted by its environment bridge, so its
    effective liveness must be gated on that env bridge — NOT on a surviving
    delivery-loop heartbeat. The operator killed the env bridge and managed agents
    stayed `available`/`online` because detached loops kept heartbeating; the hole
    was that the status compute resolved `environment_id` ONLY from the live session
    row / runtime_state, both of which are absent once the worker dies.

    Resolution order (the agent's STORED binding):
      1. the already-resolved id (session row / runtime_state.environmentId), then
      2. runtime_config.environmentId (the spawn-time binding), then
      3. the environment on the agent's machine_id that advertises its runtime.

    Returns the environments row, or None if no owning environment can be
    determined (e.g. an unbound agent with no machine/runtime match) — callers must
    NOT force offline on None (preserve the unbound `available` fall-through).
    """
    # 1. already-resolved id.
    env_id = str(resolved_environment_id or "").strip()
    # 2. spawn-time binding stored in runtime_config.
    if not env_id:
        try:
            runtime_config = _json_loads_or(agent_row["runtime_config"], {})
            env_id = str(runtime_config.get("environmentId") or "").strip()
        except Exception:
            env_id = ""
    if env_id:
        row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))).fetchone()
        if row:
            return row
    # 3. machine_id + runtime match (the environment that advertises this runtime
    #    on the agent's machine). Mirrors how spawn picks an environment.
    machine_id = str(agent_row["machine_id"] or "").strip()
    runtime = _normalize_runtime(agent_row["runtime"] or "")
    if not machine_id:
        return None
    candidates = await (await db.execute(
        "SELECT * FROM environments WHERE machine_id = ? ORDER BY last_seen DESC",
        (machine_id,),
    )).fetchall()
    for row in candidates:
        environment = _environment_record_to_dict(row)
        if _runtime_capability_for_environment(environment, runtime):
            return row
    return None


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


def _iso_add_seconds(value: str, seconds: int) -> str:
    # Compose the canonical parse/format helpers so refresh_after timestamps use
    # the same second-precision "...Z" form as _now() (what they're compared to).
    epoch = _iso_to_epoch(value)
    if not epoch:
        return ""
    return _iso_from_ms(int((epoch + max(0, int(seconds))) * 1000))


def _status_refresh_after(agent_last_seen: str, env_last_seen: str, *, idle_minutes: int, offline_minutes: int, env_offline_seconds: int) -> str:
    candidates = [
        _iso_add_seconds(agent_last_seen, int(idle_minutes or 0) * 60),
        _iso_add_seconds(agent_last_seen, int(offline_minutes or 0) * 60),
        _iso_add_seconds(env_last_seen, int(env_offline_seconds or 0)),
    ]
    candidates = [value for value in candidates if value]
    return min(candidates) if candidates else ""


async def _current_agent_session_row(db, agent_id: str):
    cursor = await db.execute(
        """
        SELECT *
        FROM agent_sessions
        WHERE agent_id = ?
          AND status NOT IN ('ended', 'completed', 'cancelled')
        ORDER BY
          CASE WHEN status IN ('running', 'recovering', 'restarting', 'cli-takeover') THEN 0 ELSE 1 END,
          last_seen DESC,
          started_at DESC
        LIMIT 1
        """,
        (agent_id,),
    )
    return await cursor.fetchone()


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


_ANSI_RE = re.compile(
    r"\x1b\][\s\S]*?(?:\x07|\x1b\\)|"
    r"\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x1b[PX^_][\s\S]*?\x1b\\|"
    r"\x1b[()][A-Za-z0-9]|"
    r"\x1b[=>]"
)


def _terminal_awaiting_input_hint(output: str) -> str:
    clean = _ANSI_RE.sub("", str(output or ""))
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
    tail = clean[-2000:].strip()
    if not tail:
        return ""
    if re.search(r"(\(y/n\)|\[y/n\]|\by/n\b|\[y/N\]|\[Y/n\]|yes/no|press\s+(enter|any key)|enter\s+to\s+confirm|are you sure|overwrite\?|\bpassword\s*:\s*$|passphrase\s*:\s*$)", tail, re.I):
        return "Awaiting console confirmation."
    if re.search(r"(use arrows|press enter to (select|confirm)|\(use arrow keys\))", tail, re.I):
        return "Awaiting console selection."
    # Claude Code can stop at an interactive prompt without emitting a formal
    # dashboard reply. This keeps the run active but no useful work is moving.
    # Do not match the normal Claude footer ("bypass permissions on",
    # "shift+tab", "for agents") by itself; that footer is present at idle
    # prompts after successful work too.
    decision_prompt = re.search(
        r"(tell me which|need (?:a )?decision|which (option|one)|choose (one|an option)|say the word)",
        tail,
        re.I,
    )
    your_call_prompt = re.search(r"your call\s*(?:[:\u2014-]|\n|$)", tail, re.I) and re.search(
        r"(decision|option|choose|execute|continue|switch|revert|debug|drive|say the word)",
        tail,
        re.I,
    )
    if decision_prompt or your_call_prompt:
        return "Awaiting console input."
    return ""


def _terminal_idle_prompt_hint(output: str) -> str:
    clean = _ANSI_RE.sub("", str(output or ""))
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
    tail = clean[-3000:].strip()
    if not tail or _terminal_awaiting_input_hint(tail):
        return ""
    marker_positions = [
        tail.lower().rfind("bypass permissions"),
        tail.lower().rfind("for agents"),
        tail.rfind("❯"),
    ]
    marker_at = max(marker_positions)
    if marker_at < 0:
        return ""
    suffix = tail[marker_at:]
    if re.search(r"(calling|cogitat|honking|thinking|running|undulating|press\s+esc|esc\s+to\s+interrupt)", suffix, re.I):
        return ""
    return "Claude PTY returned to an idle prompt without an explicit reply."


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
            try:
                turn_state_ready = int(_tb["ready"] or 0) == 1
            except (IndexError, KeyError):
                # Pre-migration row (column absent on a foreign DB schema).
                turn_state_ready = False
    except Exception:
        turn_busy = False
        turn_state_ready = False
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
    resident_bridge_stale = False
    if agent_session_mode == "resident" and "resident-run" in _row_capabilities(agent_row):
        resident_bridge_stale = not await _resident_bridge_is_fresh(
            db,
            agent_row,
            lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150),
        )
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
    has_live_worker = False
    if live_session:
        worker_row = await (await db.execute(
            """
            SELECT status, command FROM terminal_sessions
            WHERE agent_id = ?
              AND status NOT IN ('stopped', 'failed')
            ORDER BY updated_at DESC LIMIT 1
            """,
            (agent_row["id"],),
        )).fetchone()
        if worker_row:
            w_status = str(worker_row["status"] or "").strip().lower()
            w_command = str(worker_row["command"] or "")
            if w_status in {"starting", "attached", "running", "active", "idle", "recovering"}:
                if (
                    w_command in VIRTUAL_RPC_COMMAND_SET
                    or "-aify" in w_command
                    or w_command.startswith("opencode")
                ):
                    has_live_worker = True
        # Resident mode fallback: an operator-launched wrapper might
        # not register a terminal_session (it lives outside the
        # dashboard-tracked PTY). live_session is the only signal
        # available — trust it.
        if not has_live_worker and agent_session_mode == "resident":
            has_live_worker = True
    # Task 1.6 (2026-05-30): standalone-channel-sidecar deliverability gate —
    # runtime-agnostic for channel-enabled managed agents. claude's sidecar
    # runs inside the claude-aify wrapper PTY, so the terminal_sessions check
    # above is its liveness proof and this branch is a no-op for it (it has no
    # separate channel-sidecar bridge row). hermes's sidecar
    # (hermes-channel.js) is a SEPARATE process that owns no PTY — its liveness
    # proof is a fresh channel-sidecar bridge heartbeat. Without this, a
    # channel-enabled managed hermes with no live_session/terminal would have
    # has_live_worker=False and report `available` even while its sidecar is
    # actively delivering; with it, `online` is gated on REAL deliverability
    # (channelEnabled AND a live sidecar heartbeat) and falls back to
    # `available` the moment the sidecar dies — never a falsely positive online.
    # ASYMMETRY(hermes): hermes is the runtime that needs the standalone-sidecar
    # liveness probe because it has no wrapper PTY in the channel path; claude
    # is covered by its PTY terminal_session and harmlessly passes through here.
    channel_managed_no_sidecar = False
    # #166: distinguish "sidecar is alive but the console PTY is dead" (a headless
    # orphan being reaped) from a genuinely dead sidecar — they need different
    # operator-facing reasons. Both still produce `available` (not deliverable).
    channel_managed_no_console = False
    runtime_for_delivery = _normalize_runtime(agent_row["runtime"] or "")
    if (
        agent_session_mode == "managed"
        and runtime_for_delivery in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES
    ):
        # status-F1 (refined 2026-06-01, Workstream B; extended to hermes WS3
        # 2026-06-02): a managed claude/hermes worker IS its visible console PTY;
        # the channel-sidecar (claude-channel.js / the hermes delivery loop) is the
        # actual claimer that delivers. Visible-TUI is a HARD requirement, so
        # `online` REQUIRES BOTH a live console PTY AND a live channel sidecar — a
        # live console with a dead claimer is the operator-observed "online but
        # deaf" bug. A live sidecar with NO console is a headless orphan worker
        # (reaped by _reconcile_managed_worker_hygiene) → report `available`, never a
        # falsely-positive `online`. A live console with a dead sidecar is also not
        # deliverable → `available` (the original status-F1 intent, preserved).
        sidecar_live = await _has_live_channel_sidecar(db, agent_row["id"])
        console_live = await _has_live_terminal_session(db, agent_row["id"])
        if sidecar_live and console_live:
            has_live_worker = True
        else:
            has_live_worker = False
            if sidecar_live and not console_live:
                # Headless orphan: the delivery sidecar is alive but the visible
                # console PTY is gone (a visible-TUI violation being reaped by
                # _reconcile_managed_worker_hygiene). The sidecar is NOT the issue.
                channel_managed_no_console = True
            else:
                channel_managed_no_sidecar = True
    elif (
        not has_live_worker
        and agent_session_mode == "managed"
        and _channel_flag_enabled(_json_loads_or(agent_row["runtime_config"], {}))
    ):
        # Standalone channel-sidecar liveness for channel-flag runtimes that have
        # no wrapper PTY (hermes hermes-channel.js). Only fills in has_live_worker
        # when the PTY signal is absent — see ASYMMETRY(hermes) note above.
        if await _has_live_channel_sidecar(db, agent_row["id"]):
            has_live_worker = True
        else:
            channel_managed_no_sidecar = True
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
    reason = ""
    awaiting_reply = False  # set True when the agent is idle but owes a channel reply
    terminal_input_hint = ""
    if terminal_id and (active_run or (agent_session_mode == "managed" and has_live_worker)):
        try:
            terminal_row = await (await db.execute(
                "SELECT output FROM terminal_sessions WHERE id = ?",
                (terminal_id,),
            )).fetchone()
            terminal_input_hint = _terminal_awaiting_input_hint(terminal_row["output"] if terminal_row else "")
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
        session_bridge_id
        and env_bridge_id
        and session_bridge_id != env_bridge_id
        and not live_session
        and not active_run
    ):
        effective_status = "offline"
        reason = "Current environment bridge no longer owns the active session."
    elif resident_bridge_stale and not active_run:
        # A stale resident bridge means a DEAD worker → `stale`, even when the
        # agent owes a channel reply. (Previously `and not channel_pending_reply_run`
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
        # is the liveness signal: a stale bridge is a dead worker regardless of any
        # turn_busy=1, so it must derive stale BEFORE the turn_busy branch is reached.
        effective_status = "stale"
        reason = "Resident bridge heartbeat is stale or missing."
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
            if effective_status not in {"offline", "stale", "blocked"}:
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
        # Staleness checks: heartbeat-stale agents are functionally offline
        # regardless of worker presence — apply to both `online` and
        # `available`. Idle-warning only meaningful for `online` (workers
        # that haven't done anything in a while); `available` agents are
        # by definition not working, so the idle marker is redundant.
        if effective_status in {"online", "available"}:
            idle_minutes = int(settings.get("idle_minutes", 5) or 5)
            offline_minutes = int(settings.get("offline_minutes", 30) or 30)
            freshness = max(_iso_to_epoch(agent_last_seen), _iso_to_epoch(session_row["last_seen"] if session_row else ""))
            try:
                age = datetime.now(timezone.utc).timestamp() - freshness if freshness else 0
                if freshness and age > timedelta(minutes=offline_minutes).total_seconds():
                    effective_status = "offline"
                    reason = "Agent heartbeat is stale."
                elif effective_status == "online" and freshness and age > timedelta(minutes=idle_minutes).total_seconds():
                    effective_status = "idle"
                    reason = "Agent is idle."
            except Exception:
                pass
        # Task 1.6: surface WHY a channel-enabled managed agent is only
        # `available` rather than deliverable — the channel sidecar
        # (hermes-channel.js) is not heartbeating. Only annotate when we
        # haven't already attached a more specific reason (e.g. offline).
        if effective_status == "available" and channel_managed_no_console and not reason:
            reason = "Worker has no visible console (headless orphan being reaped)."
        elif effective_status == "available" and channel_managed_no_sidecar and not reason:
            reason = "No live channel sidecar heartbeat (not deliverable)."
    refresh_after = _status_refresh_after(
        agent_last_seen,
        env_last_seen,
        idle_minutes=int(settings.get("idle_minutes", 5) or 5),
        offline_minutes=int(settings.get("offline_minutes", 30) or 30),
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
    }


async def _refresh_agent_live_state(db, agent_id: str, *, settings: Optional[dict[str, Any]] = None, now: Optional[str] = None):
    row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not row:
        return None
    cache = await _compute_live_status_cache(db, row, settings=settings, now=now)
    await db.execute(
        """
        INSERT INTO agent_live_state (
            agent_id, status, reason, environment_id, session_id, terminal_id, active_run_id, refresh_after, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(agent_id) DO UPDATE SET
            status = excluded.status,
            reason = excluded.reason,
            environment_id = excluded.environment_id,
            session_id = excluded.session_id,
            terminal_id = excluded.terminal_id,
            active_run_id = excluded.active_run_id,
            refresh_after = excluded.refresh_after,
            updated_at = excluded.updated_at
        """,
        (
            agent_id,
            cache["status"],
            cache["reason"],
            cache["environment_id"],
            cache["session_id"],
            cache["terminal_id"],
            cache["active_run_id"],
            cache["refresh_after"],
            cache["updated_at"],
        ),
    )
    return cache


async def _invalidate_agent_live_state(db, agent_id: str) -> None:
    agent_id = str(agent_id or "").strip()
    if agent_id:
        await db.execute("DELETE FROM agent_live_state WHERE agent_id = ?", (agent_id,))


async def _fail_pending_terminal_controls(db, terminal_id: str, *, handled_at: str, response_text: str) -> int:
    cursor = await db.execute(
        """
        SELECT id
        FROM terminal_controls
        WHERE terminal_id = ?
          AND status IN ('pending', 'claimed')
        """,
        (terminal_id,),
    )
    rows = await cursor.fetchall()
    control_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "")]
    if not control_ids:
        return 0
    await db.executemany(
        """
        UPDATE terminal_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
        WHERE id = ?
        """,
        [(handled_at, response_text, control_id) for control_id in control_ids],
    )
    return len(control_ids)


async def _close_active_terminal_runs_for_terminal(db, terminal, terminal_status: str, *, now: Optional[str] = None, reason: str = "") -> int:
    if not terminal:
        return 0
    status = str(terminal_status or "").strip().lower()
    if status not in _TERMINAL_END_STATUSES:
        return 0
    terminal_id = str(terminal["id"] or "")
    agent_id = str(terminal["agent_id"] or "")
    if not terminal_id or not agent_id:
        return 0
    now = now or _now()
    terminal_label = status or "ended"
    run_status = "cancelled" if status in {"stopped", "cancelled"} else "failed"
    summary = reason or f"Terminal {terminal_label} before an explicit reply was recorded."
    cursor = await db.execute(
        """
        SELECT id
        FROM dispatch_runs
        WHERE target_agent = ?
          AND dispatch_mode = 'terminal'
          AND status IN ('claimed', 'running')
        """,
        (agent_id,),
    )
    rows = await cursor.fetchall()
    run_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "")]
    for run_id in run_ids:
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = ?,
                summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
                error_text = CASE WHEN ? = 'failed' AND COALESCE(error_text, '') = '' THEN ? ELSE error_text END,
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ?
              AND status IN ('claimed', 'running')
            """,
            (run_status, summary, run_status, summary, now, run_id),
        )
        await _append_dispatch_event(db, run_id, "terminal_closed", f"{summary} terminalId={terminal_id}")
    if run_ids:
        await _fail_pending_terminal_controls(db, terminal_id, handled_at=now, response_text=summary)
        await _invalidate_agent_live_state(db, agent_id)
    queued_ids: list[str] = []
    current_session = await _current_agent_session_row(db, agent_id)
    current_terminal_id = str((current_session["terminal_id"] if current_session and "terminal_id" in current_session.keys() else "") or "").strip()
    if current_terminal_id == terminal_id:
        queued_summary = reason or f"Terminal {terminal_label} before the channel bridge claimed the run."
        queued_cursor = await db.execute(
            """
            SELECT id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND execution_mode = 'channel'
              AND status = 'queued'
              AND dispatch_mode != 'message_only'
            ORDER BY requested_at ASC
            """,
            (agent_id,),
        )
        queued_rows = await queued_cursor.fetchall()
        queued_ids = [str(row["id"] or "") for row in queued_rows if str(row["id"] or "")]
        for run_id in queued_ids:
            await db.execute(
                """
                UPDATE dispatch_runs
                SET status = 'failed',
                    summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
                    error_text = CASE WHEN COALESCE(error_text, '') = '' THEN ? ELSE error_text END,
                    finished_at = COALESCE(finished_at, ?)
                WHERE id = ?
                  AND status = 'queued'
                """,
                (queued_summary, queued_summary, now, run_id),
            )
            await _append_dispatch_event(db, run_id, "terminal_closed", f"{queued_summary} terminalId={terminal_id}")
        if queued_ids:
            await _invalidate_agent_live_state(db, agent_id)
    return len(run_ids) + len(queued_ids)


def _terminal_pi_idle_prompt_hint(output: str) -> str:
    """Detect Pi (omp) idle input prompt at the tail of terminal output.

    The omp interactive prompt renders a two-line input box:

        ╭── π  > ⬢ GPT-5.5 · ◕ high > 📁 C:\\tmp > ◫ 49.1%/272K ⟲ > $... ▶──╮
        ╰─                                                                ─╯

    When this idle box appears at the tail of the buffer and there is no
    active-thinking indicator below, pi is sitting at the input prompt
    waiting for new input — meaning whatever turn was in flight is done.
    Used by _close_idle_pi_terminal_run_without_reply the same way claude's
    idle-prompt detection closes PTY-delivered runs whose interactive
    runtime returned to ready state without a structured reply event.
    """
    clean = _ANSI_RE.sub("", str(output or ""))
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
    tail = clean[-3000:]
    if not tail:
        return ""
    # The bottom-border of the omp input box. Distinctive enough that
    # plain log content won't false-positive. Both upper and lower box
    # corners must be present near the tail to confirm idle state.
    has_top = ("▶──╮" in tail) or ("π" in tail and "⬢" in tail)
    has_bottom = "╰─" in tail and "─╯" in tail
    if not (has_top and has_bottom):
        return ""
    # Bail if a streaming-thinking marker appears AFTER the idle box —
    # would mean pi went back to thinking after a momentary prompt flash.
    last_box_idx = tail.rfind("╰─")
    suffix = tail[last_box_idx:]
    if re.search(r"(thinking|cogitating|streaming|honking|press\s+esc|esc\s+to\s+interrupt)", suffix, re.I):
        return ""
    return "Pi PTY returned to an idle prompt without an explicit reply."


async def _close_idle_pi_terminal_run_without_reply(db, row, *, quiet_seconds: int = 20) -> bool:
    """Pi analog of _close_idle_claude_terminal_run_without_reply.

    Pi's interactive omp wrapper does not emit a structured turn-end
    event when running under managed_terminal_backing. Without this
    detector, PTY-delivered runs to pi sit status='running' forever
    while pi is actually idle. The reconcile sweep (startup + periodic)
    calls this on each active run; when the pi terminal output shows
    the idle input box and the buffer has been quiet for quiet_seconds,
    the run is closed as completed.
    """
    if not row:
        return False
    if str(row["dispatch_mode"] or "").strip().lower() != "terminal":
        return False
    if str(row["result_message_id"] or "").strip():
        return False
    agent_id = str(row["target_agent"] or "").strip()
    if not agent_id:
        return False
    session = await _current_agent_session_row(db, agent_id)
    runtime = str(row["runtime"] or "").strip()
    if not runtime and session and "runtime" in session.keys():
        runtime = str(session["runtime"] or "").strip()
    if _normalize_runtime(runtime) != "pi":
        return False
    terminal_id = str((session["terminal_id"] if session and "terminal_id" in session.keys() else "") or "").strip()
    if not terminal_id:
        return False
    terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
    if not terminal:
        return False
    terminal_status = str(terminal["status"] or "").strip().lower()
    if terminal_status not in _TERMINAL_ACTIVE_STATUSES:
        return False
    hint = _terminal_pi_idle_prompt_hint(terminal["output"] or "")
    if not hint:
        return False
    updated_epoch = _iso_to_epoch(str(terminal["updated_at"] or "").strip())
    run_epoch = max(
        _iso_to_epoch(row["started_at"] if "started_at" in row.keys() else ""),
        _iso_to_epoch(row["claimed_at"] if "claimed_at" in row.keys() else ""),
        _iso_to_epoch(row["requested_at"] if "requested_at" in row.keys() else ""),
    )
    if updated_epoch and run_epoch and updated_epoch < run_epoch:
        return False
    if updated_epoch and time.time() - updated_epoch < max(0, int(quiet_seconds or 0)):
        return False
    now = _now()
    summary = hint
    await db.execute(
        """
        UPDATE dispatch_runs
        SET status = 'completed',
            summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
            finished_at = COALESCE(finished_at, ?)
        WHERE id = ?
          AND status IN ('claimed', 'running')
          AND COALESCE(result_message_id, '') = ''
        """,
        (summary, now, row["id"]),
    )
    await _append_dispatch_event(db, row["id"], "terminal_closed", f"{summary} terminalId={terminal_id}")
    await _fail_pending_terminal_controls(db, terminal_id, handled_at=now, response_text=summary)
    await _invalidate_agent_live_state(db, agent_id)
    return True


async def _close_idle_claude_terminal_run_without_reply(db, row, *, quiet_seconds: int = 20) -> bool:
    if not row:
        return False
    if str(row["dispatch_mode"] or "").strip().lower() != "terminal":
        return False
    if str(row["result_message_id"] or "").strip():
        return False
    agent_id = str(row["target_agent"] or "").strip()
    if not agent_id:
        return False
    session = await _current_agent_session_row(db, agent_id)
    runtime = str(row["runtime"] or "").strip()
    if not runtime and session and "runtime" in session.keys():
        runtime = str(session["runtime"] or "").strip()
    if _normalize_runtime(runtime) != "claude-code":
        return False
    terminal_id = str((session["terminal_id"] if session and "terminal_id" in session.keys() else "") or "").strip()
    if not terminal_id:
        return False
    terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
    if not terminal:
        return False
    terminal_status = str(terminal["status"] or "").strip().lower()
    if terminal_status not in _TERMINAL_ACTIVE_STATUSES:
        return False
    hint = _terminal_idle_prompt_hint(terminal["output"] or "")
    if not hint:
        return False
    updated_epoch = _iso_to_epoch(str(terminal["updated_at"] or "").strip())
    run_epoch = max(
        _iso_to_epoch(row["started_at"] if "started_at" in row.keys() else ""),
        _iso_to_epoch(row["claimed_at"] if "claimed_at" in row.keys() else ""),
        _iso_to_epoch(row["requested_at"] if "requested_at" in row.keys() else ""),
    )
    if updated_epoch and run_epoch and updated_epoch < run_epoch:
        return False
    if updated_epoch and time.time() - updated_epoch < max(0, int(quiet_seconds or 0)):
        return False
    now = _now()
    await db.execute(
        """
        UPDATE dispatch_runs
        SET status = 'completed',
            summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
            finished_at = COALESCE(finished_at, ?)
        WHERE id = ?
          AND status IN ('claimed', 'running')
          AND COALESCE(result_message_id, '') = ''
        """,
        (hint, now, row["id"]),
    )
    await _append_dispatch_event(db, row["id"], "terminal_idle_reconciled", f"{hint} terminalId={terminal_id}")
    await _invalidate_agent_live_state(db, agent_id)
    return True


async def _refresh_expired_agent_live_states(db, *, settings: Optional[dict[str, Any]] = None, agent_ids: Optional[list[str]] = None) -> None:
    settings = settings or await _load_settings(db)
    now = _now()
    where = ""
    params: list[Any] = []
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        where = f"WHERE a.id IN ({placeholders})"
        params.extend(agent_ids)
    cursor = await db.execute(
        f"""
        SELECT a.id, ls.refresh_after
        FROM agents a
        LEFT JOIN agent_live_state ls ON ls.agent_id = a.id
        {where}
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    for row in rows:
        refresh_after = str((row["refresh_after"] if "refresh_after" in row.keys() else "") or "").strip()
        if not refresh_after or refresh_after <= now:
            await _refresh_agent_live_state(db, row["id"], settings=settings, now=now)


async def _managed_environment_status(db, row) -> tuple[str, str, str]:
    if not row or _normalize_session_mode(row["session_mode"] or "resident") != "managed":
        return "", "", ""
    runtime_state = _json_loads_or(row["runtime_state"], {})
    environment_id = str(runtime_state.get("environmentId") or "").strip()
    if not environment_id:
        session_cursor = await db.execute(
            """
            SELECT environment_id
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (row["id"],),
        )
        session = await session_cursor.fetchone()
        environment_id = str((session["environment_id"] if session else "") or "").strip()
    if not environment_id:
        return "", "", ""

    settings = await _load_settings(db)
    env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
    env = await env_cursor.fetchone()
    env_status = _environment_effective_status(
        env,
        offline_seconds=settings.get("environment_offline_seconds", 90),
    ) if env else "offline"
    env_bridge = str((env["bridge_id"] if env else "") or "").strip()
    return environment_id, env_status, env_bridge


async def _repair_spawn_requests_from_initial_dispatch_failures(db) -> int:
    cursor = await db.execute(
        """
        SELECT *
        FROM spawn_requests
        WHERE status = 'running'
          AND COALESCE(initial_message, '') != ''
          AND COALESCE(error, '') = ''
        """
    )
    repaired = 0
    for spawn in await cursor.fetchall():
        started_at = spawn["started_at"] or spawn["updated_at"] or spawn["created_at"]
        run_cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE target_agent = ?
              AND requested_at >= ?
            ORDER BY requested_at ASC
            LIMIT 1
            """,
            (spawn["agent_id"], started_at),
        )
        run = await run_cursor.fetchone()
        if not run or str(run["status"] or "").lower() not in {"failed", "cancelled"}:
            continue
        error = (run["error_text"] or run["summary"] or f"Initial dispatch {run['status']}").strip()
        now = _now()
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                error = ?,
                finished_at = COALESCE(finished_at, ?),
                updated_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (f"Initial brief failed: {error}", run["finished_at"] or now, now, spawn["id"]),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'failed',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE spawn_request_id = ?
              AND status IN ('starting', 'running')
            """,
            (run["finished_at"] or now, now, spawn["id"]),
        )
        repaired += 1
    if repaired:
        await db.commit()
    return repaired


async def _repair_superseded_recovering_sessions(db) -> int:
    now = _now()
    cursor = await db.execute(
        """
        SELECT old.id
        FROM agent_sessions old
        WHERE old.status IN ('starting', 'recovering', 'restarting')
          AND EXISTS (
            SELECT 1
            FROM agent_sessions current
            WHERE current.agent_id = old.agent_id
              AND current.id != old.id
              AND current.status = 'running'
              AND COALESCE(NULLIF(current.last_seen, ''), NULLIF(current.started_at, ''), '') >=
                  COALESCE(NULLIF(old.last_seen, ''), NULLIF(old.started_at, ''), '')
          )
        """
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    for row in rows:
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'ended',
                ended_at = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?),
                last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
            WHERE id = ?
              AND status IN ('starting', 'recovering', 'restarting')
            """,
            (now, now, row["id"]),
        )
    await db.commit()
    return len(rows)


async def _repair_current_session_freshness(db) -> int:
    cursor = await db.execute(
        """
        SELECT id, last_seen, runtime_state
        FROM agents
        WHERE session_mode = 'managed'
          AND runtime_state IS NOT NULL
          AND runtime_state != ''
          AND runtime_state != '{}'
        """
    )
    repaired = 0
    for row in await cursor.fetchall():
        runtime_state = _json_loads_or(row["runtime_state"], {})
        if not (runtime_state.get("spawnRequestId") or runtime_state.get("environmentId")):
            continue
        before = db.total_changes
        await _touch_current_agent_session(db, row["id"], runtime_state, row["last_seen"] or _now())
        if db.total_changes > before:
            repaired += 1
    if repaired:
        await db.commit()
    return repaired


async def _repair_terminal_session_consistency(db) -> int:
    now = _now()
    active_statuses = ("starting", "attached", "running", "active", "idle")
    repaired = 0

    legacy_cursor = await db.execute(
        f"""
        SELECT id, agent_id
        FROM terminal_sessions
        WHERE runtime = 'claude-code'
          AND status IN ({",".join("?" for _ in active_statuses)})
          AND COALESCE(command, '') != ''
          AND command NOT LIKE '%claude-aify%'
        """,
        active_statuses,
    )
    legacy_rows = await legacy_cursor.fetchall()
    for row in legacy_rows:
        terminal_id = str(row["id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        if not terminal_id:
            continue
        reason = "Released legacy raw Claude terminal during session reconciliation; Claude backing must start through claude-aify."
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'failed',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                terminal_status = 'failed',
                last_seen = ?
            WHERE terminal_id = ?
            """,
            (now, terminal_id),
        )
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    # Exclude virtual rpc terminals from PTY-status mirroring. The
    # synth feed for managed pi/hermes/codex/opencode has a different
    # lifecycle from a real node-pty wrapper: it survives across
    # dispatch boundaries as the operator-visibility surface, while
    # the agent_sessions.terminal_status field can carry stale state
    # from a previous wrapper PTY for the same agent. Operator-
    # reported 2026-05-22: hermes synth terminal got marked stopped
    # within seconds of creation because agent_sessions.terminal_status
    # had a leftover 'stopped' from earlier hermes-aify wrapper PTYs.
    mismatch_cursor = await db.execute(
        f"""
        SELECT t.id, t.agent_id, s.terminal_status
        FROM terminal_sessions t
        JOIN agent_sessions s ON s.terminal_id = t.id
        WHERE t.status IN ({",".join("?" for _ in active_statuses)})
          AND s.terminal_status IN ('stopped', 'failed')
          AND t.command NOT IN ({",".join("?" for _ in VIRTUAL_RPC_COMMAND_SET)})
        """,
        (*active_statuses, *VIRTUAL_RPC_COMMAND_SET),
    )
    mismatch_rows = await mismatch_cursor.fetchall()
    for row in mismatch_rows:
        terminal_id = str(row["id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        terminal_status = str(row["terminal_status"] or "").strip().lower()
        if not terminal_id or terminal_status not in {"stopped", "failed"}:
            continue
        reason = f"Terminal reconciled because owner session is {terminal_status}."
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = ?,
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (terminal_status, now, now, reason, terminal_id),
        )
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    orphan_cursor = await db.execute(
        f"""
        SELECT t.id, t.agent_id
        FROM terminal_sessions t
        LEFT JOIN agent_sessions s ON s.terminal_id = t.id
        WHERE t.status IN ({",".join("?" for _ in active_statuses)})
          AND s.id IS NULL
        """,
        active_statuses,
    )
    orphan_rows = await orphan_cursor.fetchall()
    for row in orphan_rows:
        terminal_id = str(row["id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        if not terminal_id:
            continue
        reason = "Terminal reconciled because it is not referenced by any current session."
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    inactive_binding_cursor = await db.execute(
        """
        SELECT s.id AS session_id,
               s.agent_id AS agent_id,
               s.terminal_id AS terminal_id,
               s.terminal_status AS session_terminal_status,
               t.status AS terminal_status
        FROM agent_sessions s
        JOIN terminal_sessions t ON t.id = s.terminal_id
        WHERE COALESCE(s.terminal_id, '') != ''
          AND (
            LOWER(COALESCE(s.terminal_status, '')) IN ('stopped', 'failed')
            OR LOWER(COALESCE(t.status, '')) IN ('stopped', 'failed')
          )
        """
    )
    inactive_binding_rows = await inactive_binding_cursor.fetchall()
    for row in inactive_binding_rows:
        session_id = str(row["session_id"] or "").strip()
        agent_id = str(row["agent_id"] or "").strip()
        terminal_id = str(row["terminal_id"] or "").strip()
        if not session_id or not terminal_id:
            continue
        reason = "Cleared stopped Console terminal as current session binding."
        if agent_id:
            await _clear_console_terminal_binding(db, agent_id, terminal_id, now=now)
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                owner_bridge_id = '',
                terminal_id = '',
                terminal_status = '',
                terminal_command = '',
                terminal_workspace = '',
                last_seen = ?
            WHERE id = ?
              AND terminal_id = ?
            """,
            (now, session_id, terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_consistency_repaired",
            json.dumps({"reason": reason}),
        )
        repaired += 1

    if repaired:
        await db.commit()
    return repaired


def _runtime_capability_for_environment(environment: dict[str, Any], runtime: str) -> Optional[dict[str, Any]]:
    normalized = _normalize_runtime(runtime)
    for item in environment.get("runtimes") or []:
        if _normalize_runtime(item.get("runtime") or "") == normalized:
            return item
    return None


def _environment_supports_terminal(environment: dict[str, Any], runtime: str) -> bool:
    if not bool(environment.get("terminal")) or not bool(environment.get("pty")):
        return False
    allowed = [
        _normalize_runtime(str(item or ""))
        for item in (environment.get("terminalRuntimes") or [])
        if str(item or "").strip()
    ]
    if allowed and _normalize_runtime(runtime) not in allowed:
        return False
    return True


def _environment_uses_windows_paths(environment: dict[str, Any]) -> bool:
    text = " ".join(
        str(environment.get(key) or "")
        for key in ("id", "os", "kind", "machineId")
    ).lower()
    if "win32" in text or "windows" in text:
        return True
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    return any(re.match(r"^[A-Za-z]:[\\/]", root) for root in roots)


def _normalize_workspace_for_environment(environment: dict[str, Any], workspace: str) -> str:
    value = str(workspace or "").strip()
    if not value:
        return ""
    if _environment_uses_windows_paths(environment):
        return value
    return value.replace("\\", "/")


def _workspace_root_for(environment: dict[str, Any], workspace: str) -> str:
    workspace_value = _normalize_workspace_for_environment(environment, workspace)
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    if not workspace_value or not roots:
        return roots[0] if roots else ""
    normalized_workspace = workspace_value.replace("\\", "/").rstrip("/")
    for root in roots:
        normalized_root = root.replace("\\", "/").rstrip("/")
        if normalized_workspace == normalized_root or normalized_workspace.startswith(normalized_root + "/"):
            return root
    raise HTTPException(400, f'Workspace "{workspace_value}" is outside the roots advertised by environment "{environment.get("id")}"')


def _workspace_for_environment(environment: dict[str, Any], requested_workspace: Optional[str], fallback_workspace: Optional[str] = "") -> tuple[str, str]:
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    workspace = _normalize_workspace_for_environment(environment, requested_workspace or fallback_workspace or "")
    if not workspace:
        workspace = roots[0] if roots else ""
    try:
        workspace_root = _workspace_root_for(environment, workspace)
    except HTTPException:
        if requested_workspace:
            raise
        workspace = _normalize_workspace_for_environment(environment, roots[0] if roots else "")
        workspace_root = _workspace_root_for(environment, workspace)
    if not workspace and workspace_root:
        workspace = workspace_root
    return workspace, workspace_root


def _normalize_roots(roots: Optional[list[str]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for root in roots or []:
        value = str(root or "").strip()
        if not value or value.startswith("-"):
            continue
        key = value.replace("\\", "/").rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _spawn_spec_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "environmentId": row["environment_id"],
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "model": row["model"] or "",
        "profile": row["profile"] or "",
        "mode": row["mode"] or "managed-warm",
        "systemPrompt": row["system_prompt"] or "",
        "instructions": row["standing_instructions"] or "",
        "envVars": _json_loads_or(row["env_vars"], {}),
        "channelIds": _json_loads_or(row["channel_ids"], []),
        "budgetPolicy": _json_loads_or(row["budget_policy"], {}),
        "contextPolicy": _json_loads_or(row["context_policy"], {}),
        "restartPolicy": _json_loads_or(row["restart_policy"], {}),
        "metadata": _json_loads_or(row["metadata"], {}),
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
    }


def _spawn_request_to_dict(row, spec: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "spawnSpecId": row["spawn_spec_id"],
        "createdBy": row["created_by"] or "",
        "environmentId": row["environment_id"],
        "agentId": row["agent_id"],
        "role": row["role"] or "coder",
        "name": row["name"] or "",
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "workspaceRoot": row["workspace_root"] or "",
        "initialMessage": row["initial_message"] or "",
        "priority": row["priority"] or "normal",
        "subject": row["subject"] or "",
        "mode": row["mode"] or "managed-warm",
        "resumePolicy": row["resume_policy"] or "native_first",
        "status": row["status"] or "queued",
        "claimedByBridgeId": row["claimed_by_bridge_id"] or "",
        "claimMachineId": row["claim_machine_id"] or "",
        "processId": row["process_id"] or "",
        "sessionHandle": row["session_handle"] or "",
        "sessionId": row["session_id"] or "",
        "error": row["error"] or "",
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
        "claimedAt": row["claimed_at"] or "",
        "startedAt": row["started_at"] or "",
        "finishedAt": row["finished_at"] or "",
    }
    if spec is not None:
        payload["spawnSpec"] = spec
    return payload


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
        "status": row["status"] or "",
        "requestedBy": row["requested_by"] or "",
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
        "stoppedAt": row["stopped_at"] or "",
        "error": row["error"] or "",
    }


def _terminal_event_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "terminalId": row["terminal_id"],
        "eventType": row["event_type"],
        "body": row["body"] or "",
        "createdAt": row["created_at"] or "",
    }


def _terminal_control_to_dict(
    row,
    *,
    pid: str = "",
    agent_id: str = "",
    runtime: str = "",
    session_mode: str = "",
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "terminalId": row["terminal_id"],
        "environmentId": row["environment_id"],
        "bridgeId": row["bridge_id"] or "",
        "action": row["action"],
        "body": row["body"] or "",
        "cols": int(row["cols"] or 0),
        "rows": int(row["rows"] or 0),
        "status": row["status"] or "",
        "requestedBy": row["requested_by"] or "",
        "requestedAt": row["requested_at"] or "",
        "claimedAt": row["claimed_at"] or "",
        "handledAt": row["handled_at"] or "",
        "error": row["error"] or "",
        # Stored PTY root pid for the target terminal (terminal_sessions.
        # process_id). Lets a claiming bridge kill an orphaned PTY by-pid on a
        # `stop` control when it never owned the PTY in its in-memory Map
        # (owning bridge restarted/died). Empty when unknown.
        "pid": str(pid or ""),
        # Target terminal's agent + runtime, and the agent's session_mode, so a
        # claiming bridge can detect a MANAGED-HERMES `stop` and run the triad
        # teardown (gateway/loop/daemon), not just the PTY stop (fix/hermes-leak
        # P2). Empty when the terminal/agent is gone (e.g. claimed after REMOVE
        # deleted the agent) — REMOVE therefore stamps the body sentinel below so
        # the triad reap still fires.
        "agentId": str(agent_id or ""),
        "runtime": str(runtime or ""),
        "sessionMode": str(session_mode or ""),
    }


def _trim_terminal_output(text: str, max_chars: int = 65536) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _row_get(row, key, default=None):
    """Safely fetch a field from either a dict or a sqlite3.Row."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return value if value is not None else default


def _merge_runtime_policy_for_wrapper_reregister(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Keep durable model/effort policy when a wrapper child refreshes live metadata."""
    previous = existing if isinstance(existing, dict) else {}
    current = incoming if isinstance(incoming, dict) else {}
    durable_previous = {key: value for key, value in previous.items() if key not in _RUNTIME_CONFIG_LIVE_KEYS}
    return {**durable_previous, **current}


async def _compute_agent_status(row, idle_minutes: int, offline_minutes: int, db=None):
    # Single source of truth: delegate to the live-state engine that
    # list_agents/get_agent already use, so write endpoints (heartbeat,
    # register, dispatch status) never disagree with the dashboard about
    # whether an agent is active/idle/offline. The db-less fallback below is
    # only the minimal heartbeat heuristic for callers without a connection.
    status = row["status"]
    if status in _MANUAL_STATUSES:
        return status
    if db is not None:
        cache = await _refresh_agent_live_state(db, row["id"])
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

    if status != "stale":
        try:
            last = datetime.fromisoformat(str(row["last_seen"] or "").replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - last
            if age > timedelta(minutes=offline_minutes):
                status = "offline"
            elif age > timedelta(minutes=idle_minutes):
                status = "idle"
        except Exception:
            pass
    return status


async def _load_settings(db):
    settings = {**DEFAULT_SETTINGS}
    sc = await db.execute("SELECT key, value FROM settings")
    for row in await sc.fetchall():
        try:
            settings[row["key"]] = json.loads(row["value"])
        except Exception:
            pass
    return settings


async def _apply_managed_runtime_defaults(db, settings: dict[str, Any]) -> None:
    defaults = [
        ("claude-code", settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"]), settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]),
        ("codex", settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"]), settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]),
        ("pi", settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"]), settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]),
    ]
    for runtime, model, effort in defaults:
        model = str(model or "").strip()
        effort = str(effort or "").strip()
        await db.execute(
            """
            UPDATE agents
            SET model = ?
            WHERE runtime = ?
              AND (session_mode = 'managed' OR launch_mode = 'managed' OR managed_by != '')
            """,
            (model, runtime),
        )
        cursor = await db.execute(
            """
            SELECT id, runtime_config
            FROM agents
            WHERE runtime = ?
              AND (session_mode = 'managed' OR launch_mode = 'managed' OR managed_by != '')
            """,
            (runtime,),
        )
        for row in await cursor.fetchall():
            runtime_config = _json_loads_or(row["runtime_config"], {})
            runtime_config["effort"] = effort
            await db.execute(
                "UPDATE agents SET runtime_config = ? WHERE id = ?",
                (json.dumps(runtime_config), row["id"]),
            )
        await db.execute("UPDATE spawn_specs SET model = ? WHERE runtime = ?", (model, runtime))
        spec_cursor = await db.execute("SELECT id, metadata FROM spawn_specs WHERE runtime = ?", (runtime,))
        for row in await spec_cursor.fetchall():
            metadata = _json_loads_or(row["metadata"], {})
            runtime_config = metadata.get("runtimeConfig") if isinstance(metadata.get("runtimeConfig"), dict) else {}
            runtime_config = {**runtime_config, "effort": effort}
            metadata = {**metadata, "runtimeConfig": runtime_config}
            await db.execute(
                "UPDATE spawn_specs SET metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata), _now(), row["id"]),
            )


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
    c = await db.execute(
        """
        SELECT a.*, ls.status AS live_status, ls.reason AS live_reason, ls.refresh_after AS live_refresh_after
        FROM agents a
        LEFT JOIN agent_live_state ls ON ls.agent_id = a.id
        WHERE a.id = ?
        """,
        (recipient_id,),
    )
    row = await c.fetchone()
    if not row:
        return None
    unread_map = await _get_unread_count_map(db, [recipient_id])
    dispatch_state = await _get_dispatch_state_map(db, [recipient_id])
    return _agent_record_to_dict(row, row["live_status"] if "live_status" in row.keys() else row["status"], unread_map.get(recipient_id, 0), dispatch_state.get(recipient_id))


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
                hint = _dispatch_fix_hint(recipient_id, row, "resident bridge is stale; switch to managed or restart the resident wrapper")
                hint["recipientStatus"] = "stale"
                not_started.append(hint)
                continue

        dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
        active = dispatch_state.get("activeRun")
        if active and await _discard_unusable_active_run(db, recipient_id, active):
            dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
        base_status = await _compute_agent_status(
            row,
            settings.get("idle_minutes", 5),
            settings.get("offline_minutes", 30),
            db,
        )
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
        # undeliverable for the backstop window. `_managed_target_is_deaf` and the
        # lease helpers remain for status/deliverability use and no longer reject a
        # send.
        launchable.append((recipient_id, execution_mode))

    return launchable, not_started


async def _append_dispatch_event(db, run_id: str, event_type: str, body: str = ""):
    await db.execute(
        "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
        (run_id, event_type, body or "", _now())
    )


_TERMINAL_EVENT_CAP = 500
_TERMINAL_EVENT_PRUNE_EVERY = 200
_terminal_event_counts: dict[str, int] = {}


async def _append_terminal_event(db, terminal_id: str, event_type: str, body: str = ""):
    await db.execute(
        "INSERT INTO terminal_events (terminal_id, event_type, body, created_at) VALUES (?,?,?,?)",
        (terminal_id, event_type, body or "", _now()),
    )
    # terminal_events gets a row per flushed output chunk and is only ever read
    # back LIMIT ~200; without pruning it grows unbounded per terminal for the
    # life of the DB. Amortize the prune (every Nth insert) to keep it bounded
    # without paying a DELETE on every chunk.
    count = _terminal_event_counts.get(terminal_id, 0) + 1
    if count >= _TERMINAL_EVENT_PRUNE_EVERY:
        _terminal_event_counts[terminal_id] = 0
        await db.execute(
            """
            DELETE FROM terminal_events
            WHERE terminal_id = ?
              AND id NOT IN (
                SELECT id FROM terminal_events
                WHERE terminal_id = ?
                ORDER BY id DESC
                LIMIT ?
              )
            """,
            (terminal_id, terminal_id, _TERMINAL_EVENT_CAP),
        )
    else:
        _terminal_event_counts[terminal_id] = count


async def _clear_console_terminal_binding(db, agent_id: str, terminal_id: str, *, now: Optional[str] = None) -> None:
    agent_id = str(agent_id or "").strip()
    terminal_id = str(terminal_id or "").strip()
    if not agent_id or not terminal_id:
        return
    row = await (await db.execute("SELECT runtime_state, status_note FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not row:
        return
    runtime_state = _json_loads_or(row["runtime_state"], {})
    console_terminal = runtime_state.get("consoleTerminal") if isinstance(runtime_state, dict) else None
    if not isinstance(console_terminal, dict) or str(console_terminal.get("terminalId") or "").strip() != terminal_id:
        return
    runtime_state.pop("consoleTerminal", None)
    status_note = str(row["status_note"] or "").strip()
    if status_note == "Dashboard Console PTY attached.":
        status_note = ""
    await db.execute(
        """
        UPDATE agents
        SET runtime_state = ?,
            status_note = ?,
            last_seen = ?
        WHERE id = ?
        """,
        (json.dumps(runtime_state), status_note, now or _now(), agent_id),
    )
    await _invalidate_agent_live_state(db, agent_id)


async def _append_terminal_control(
    db,
    *,
    terminal_id: str,
    environment_id: str,
    bridge_id: str,
    action: str,
    requested_by: str = "dashboard",
    body: str = "",
    cols: int = 0,
    rows: int = 0,
) -> str:
    control_id = f"termctl_{int(time.time() * 1000)}_{next(_CONTROL_ID_COUNTER):06d}_{uuid.uuid4().hex[:8]}"
    await db.execute(
        """
        INSERT INTO terminal_controls (
            id, terminal_id, environment_id, bridge_id, action, body, cols, rows, status, requested_by, requested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            control_id,
            terminal_id,
            environment_id,
            bridge_id,
            action,
            body or "",
            int(cols or 0),
            int(rows or 0),
            "pending",
            requested_by or "dashboard",
            _now(),
        ),
    )
    return control_id


def _terminal_status_transition(current_status: str, next_status: str) -> str:
    current = str(current_status or "").strip().lower()
    next_value = str(next_status or "").strip().lower()
    if not next_value:
        return ""
    if current in _TERMINAL_MONOTONIC_STATUSES and next_value in _TERMINAL_ACTIVE_STATUSES:
        return ""
    return next_value


async def _append_terminal_output(db, terminal, output: str, *, status: str = "", seq: Optional[int] = None):
    chunk = str(output or "")
    if not chunk and not status:
        return
    current = terminal["output"] if "output" in terminal.keys() else ""
    next_output = _trim_terminal_output(f"{current or ''}{chunk}")
    updates = ["output = ?", "updated_at = ?"]
    params: list[Any] = [next_output, _now()]
    if seq is not None:
        updates.append("output_seq = ?")
        params.append(int(seq))
    next_status = _terminal_status_transition(terminal["status"] if "status" in terminal.keys() else "", status)
    if next_status:
        updates.append("status = ?")
        params.append(next_status)
        if next_status in {"stopped", "failed"}:
            updates.append("stopped_at = COALESCE(stopped_at, ?)")
            params.append(_now())
    params.append(terminal["id"])
    await db.execute(
        f"UPDATE terminal_sessions SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    if chunk:
        await _append_terminal_event(db, terminal["id"], "terminal_output", chunk[-2000:])
        await _maybe_auto_confirm_claude_dev_channel_prompt(db, terminal, next_output)


async def _maybe_auto_confirm_claude_dev_channel_prompt(db, terminal, full_output: str) -> None:
    """Reactive dev-channel prompt confirmation.

    Claude with `--dangerously-load-development-channels server:...` shows
    an interactive menu at boot:

        WARNING: Loading development channels
        ...
        Channels: server:aify-comms-channel
        ❯ 1. I am using this for local development
          2. ...

    The earlier blind \\r enqueue fires before this menu appears and ends
    up consumed by some other prompt. The right fix is to react to the
    actual prompt text in the terminal output: when this menu text shows
    up AND we haven't fired auto-confirm for this terminal yet, enqueue
    `1\\r` to explicitly pick the local-development option. The audit
    event guards against re-firing within the same terminal session.

    Only fires for claude-code wrappers; only when the setting is on.
    """
    if not terminal:
        return
    runtime = _normalize_runtime(terminal["runtime"] if "runtime" in terminal.keys() else "")
    if runtime != "claude-code":
        return
    # Only fire for FRESH wrappers — the dev-channel menu only appears
    # right after Claude boots. Match only when the terminal was created
    # less than 30s ago. Without this guard, the detector would also
    # fire when Claude's conversation later contains the menu text
    # verbatim (e.g., Claude explaining channels), causing spurious
    # "1\r" inputs into a live conversation — what the operator
    # described as "1's randomly entered into console".
    created_at_iso = terminal["created_at"] if "created_at" in terminal.keys() else ""
    created_epoch = _iso_to_epoch(str(created_at_iso or ""))
    if not created_epoch or (time.time() - created_epoch) > 30:
        return
    # Stripped scan-ready view of the recent output — ANSI sequences split
    # the menu line in node-pty output, so collapse them before matching.
    tail = full_output[-6000:] if full_output else ""
    if not tail:
        return
    # Strip ANSI CSI/OSC so the text-only match is reliable.
    stripped = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", tail)
    stripped = re.sub(r"\x1b[\]\^\\]\\d*;?[^\x07]*\x07?", "", stripped)
    # Require BOTH a development-channels warning AND a local-development
    # menu option to be present. Each alone could appear in normal Claude
    # conversation; both together near startup only happens for the
    # actual dev-channel approval menu. Pattern matching is intentionally
    # permissive to survive minor upstream text changes — operator-
    # reported 2026-05-22 "Claude dev-channel auto-confirm might not
    # work" probably traces to a text shift in newer Claude Code.
    warning_lower = stripped.lower()
    has_warning = (
        "loading development channels" in warning_lower
        or "loading dev channels" in warning_lower
        or "development channels enabled" in warning_lower
        or "dev-channel" in warning_lower
    )
    has_menu_option = (
        "i am using this for local development" in warning_lower
        or "using this for local development" in warning_lower
        or ("local development" in warning_lower and "channel" in warning_lower)
    )
    if not (has_warning and has_menu_option):
        return
    # Idempotency: if we already fired for this terminal session, stop.
    prior = await (await db.execute(
        "SELECT 1 FROM terminal_events WHERE terminal_id = ? AND event_type = ? LIMIT 1",
        (terminal["id"], "dev_channel_prompt_auto_confirmed"),
    )).fetchone()
    if prior:
        return
    # Gate on the setting (default true post-c895ba1).
    settings = await _load_settings(db)
    if not bool(settings.get("console_auto_confirm_claude_dev_channels", DEFAULT_SETTINGS["console_auto_confirm_claude_dev_channels"])):
        return
    # Record the audit event NOW (idempotency guard) but DEFER the actual
    # input by 2 seconds. Live testing showed firing immediately at the
    # first chunk containing the menu text races with Claude's menu
    # interactive-ready state — the "1\r" arrived while the menu was
    # still rendering and Claude apparently dismissed/ignored it. The
    # 2s deferral lets Claude finish drawing the menu and become
    # interactive before our input lands. Operator's suggested timing.
    environment_id = terminal["environment_id"] if "environment_id" in terminal.keys() else ""
    bridge_id = terminal["bridge_id"] if "bridge_id" in terminal.keys() else ""
    terminal_id_value = terminal["id"]
    await _append_terminal_event(
        db,
        terminal_id_value,
        "dev_channel_prompt_auto_confirmed",
        json.dumps({"reason": "reactive prompt-text match (deferred 2s)", "sent": "1\\r"}),
    )

    async def _deferred_send() -> None:
        try:
            await asyncio.sleep(2.0)
            inner_db = await get_db()
            try:
                await _append_terminal_control(
                    inner_db,
                    terminal_id=terminal_id_value,
                    environment_id=environment_id or "",
                    bridge_id=bridge_id or "",
                    action="input",
                    requested_by="dev-channel-auto-confirm",
                    body="1\r",
                )
                await inner_db.commit()
            finally:
                await inner_db.close()
        except Exception:
            # Best-effort. If the deferred send fails the audit event still
            # records the attempt; next observed menu (e.g., on a fresh
            # wrapper) gets another chance.
            pass

    asyncio.create_task(_deferred_send())



class TerminalOutputWriteQueue:
    def __init__(
        self,
        *,
        idle_flush_ms: int = 4,
        max_latency_ms: int = 24,
        max_batch_chars: int = 16 * 1024,
        max_pending_chars: int = 256 * 1024,
    ):
        self.idle_flush_seconds = max(0.001, idle_flush_ms / 1000)
        self.max_latency_seconds = max(self.idle_flush_seconds, max_latency_ms / 1000)
        self.max_batch_chars = max(1024, int(max_batch_chars))
        self.max_pending_chars = max(self.max_batch_chars, int(max_pending_chars))
        self._pending: dict[str, dict[str, Any]] = {}
        self._idle_handles: dict[str, asyncio.Handle] = {}
        self._max_handles: dict[str, asyncio.Handle] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}
        # Highest seq ever issued per terminal. Guarantees strict monotonicity
        # across pending-state recreation even if a concurrent request reads a
        # stale output_seq from the DB while a prior flush hasn't committed yet
        # (otherwise seq could regress and the dashboard's seq-dedupe would
        # silently drop fresh output).
        self._seq_floor: dict[str, int] = {}
        # Set by the output endpoint so the queue can emit ONE ordered,
        # gap-free terminal_output broadcast per flush. Per-POST broadcast
        # reordered vs seq under concurrency, causing the dashboard's
        # seq-dedupe to drop frames -> ANSI desync -> scrambled console.
        self.ws_manager = None
        self._lock = asyncio.Lock()

    async def enqueue(self, terminal_id: str, output: str = "", *, status: str = "", base_seq: int = 0, autoschedule: bool = True) -> int:
        chunk = str(output or "")
        terminal_status = str(status or "").strip()
        if not terminal_id or (not chunk and not terminal_status):
            return 0
        flush_now = False
        async with self._lock:
            state = self._pending.get(terminal_id)
            if not state:
                seq_start = max(int(base_seq or 0), int(self._seq_floor.get(terminal_id, 0)))
                state = {"chunks": deque(), "chars": 0, "status": "", "dropped": 0, "last_seq": seq_start}
                self._pending[terminal_id] = state
                if autoschedule:
                    self._schedule_max_flush_locked(terminal_id)
            state["last_seq"] = int(state.get("last_seq") or 0) + 1
            self._seq_floor[terminal_id] = state["last_seq"]
            if chunk:
                state["chunks"].append(chunk)
                state["chars"] += len(chunk)
                self._bound_pending_locked(state)
            if terminal_status:
                state["status"] = terminal_status
            if not autoschedule:
                return int(state["last_seq"])
            flush_now = state["chars"] >= self.max_batch_chars or terminal_status in {"stopped", "failed"}
            if flush_now:
                self._schedule_flush_locked(terminal_id, delay=0)
            else:
                self._schedule_idle_flush_locked(terminal_id)
            return int(state["last_seq"])

    def _bound_pending_locked(self, state: dict[str, Any]) -> None:
        chunks = state["chunks"]
        while state["chars"] > self.max_pending_chars and chunks:
            removed = chunks.popleft()
            removed_len = len(removed)
            state["chars"] -= removed_len
            state["dropped"] += removed_len

    def _schedule_idle_flush_locked(self, terminal_id: str) -> None:
        handle = self._idle_handles.pop(terminal_id, None)
        if handle:
            handle.cancel()
        self._idle_handles[terminal_id] = asyncio.get_running_loop().call_later(
            self.idle_flush_seconds,
            self._schedule_flush_from_timer,
            terminal_id,
        )

    def _schedule_max_flush_locked(self, terminal_id: str) -> None:
        handle = self._max_handles.pop(terminal_id, None)
        if handle:
            handle.cancel()
        self._max_handles[terminal_id] = asyncio.get_running_loop().call_later(
            self.max_latency_seconds,
            self._schedule_flush_from_timer,
            terminal_id,
        )

    def _track_flush_task(self, terminal_id: str, task: asyncio.Task) -> None:
        self._flush_tasks[terminal_id] = task
        task.add_done_callback(lambda done, key=terminal_id: self._on_flush_done(key, done))

    def _on_flush_done(self, terminal_id: str, task: asyncio.Task) -> None:
        self._flush_tasks.pop(terminal_id, None)
        try:
            task.result()
        except BaseException:
            if terminal_id in self._pending:
                try:
                    asyncio.get_running_loop().call_later(0.1, self._schedule_flush_from_timer, terminal_id)
                except RuntimeError:
                    pass

    def _schedule_flush_from_timer(self, terminal_id: str) -> None:
        try:
            self._track_flush_task(terminal_id, asyncio.create_task(self.flush_terminal(terminal_id)))
        except RuntimeError:
            # No active loop; the next explicit flush will persist the backlog.
            return

    def _schedule_flush_locked(self, terminal_id: str, *, delay: float) -> None:
        next_delay = delay if delay > 0 else 0.001
        asyncio.get_running_loop().call_later(next_delay, self._schedule_flush_from_timer, terminal_id)

    async def flush_terminal(self, terminal_id: str) -> None:
        existing = self._flush_tasks.get(terminal_id)
        if existing and existing is not asyncio.current_task():
            await asyncio.shield(existing)
            return
        async with self._lock:
            state = self._pending.pop(terminal_id, None)
            idle_handle = self._idle_handles.pop(terminal_id, None)
            max_handle = self._max_handles.pop(terminal_id, None)
            if idle_handle:
                idle_handle.cancel()
            if max_handle:
                max_handle.cancel()
        if not state:
            return
        prefix = ""
        if state["dropped"]:
            prefix = f"[aify-comms dropped {state['dropped']} chars from terminal output backlog]\n"
        output = prefix + "".join(state["chunks"])
        status = state["status"]
        seq = int(state.get("last_seq") or 0)
        try:
            await self._write_terminal_output(terminal_id, output, status=status, seq=seq)
        except BaseException:
            await self._requeue_front(terminal_id, output, status=status, seq=seq)
            raise

    async def _requeue_front(self, terminal_id: str, output: str, *, status: str = "", seq: int = 0) -> None:
        if not output and not status:
            return
        async with self._lock:
            state = self._pending.get(terminal_id)
            if not state:
                state = {"chunks": deque(), "chars": 0, "status": "", "dropped": 0, "last_seq": int(seq or 0)}
                self._pending[terminal_id] = state
            if output:
                state["chunks"].appendleft(output)
                state["chars"] += len(output)
                self._bound_pending_locked(state)
            if status:
                state["status"] = status
            if seq:
                state["last_seq"] = max(int(state.get("last_seq") or 0), int(seq))

    async def _write_terminal_output(self, terminal_id: str, output: str, *, status: str = "", seq: int = 0) -> None:
        db = await get_db()
        try:
            # Include runtime + environment_id + bridge_id so reactive
            # detectors inside _append_terminal_output (dev-channel
            # auto-confirm + pi idle-prompt close) can see what runtime
            # the buffer belongs to and where to enqueue follow-up
            # controls. Pre-fix this SELECT was id/session/agent/output/
            # status/output_seq only, which silently disabled the
            # detectors because terminal["runtime"] was empty.
            terminal = await (await db.execute(
                """
                SELECT id, session_id, agent_id, environment_id, bridge_id, runtime,
                       output, status, output_seq
                FROM terminal_sessions WHERE id = ?
                """,
                (terminal_id,),
            )).fetchone()
            if not terminal:
                return
            await _append_terminal_output(db, terminal, output, status=status, seq=seq or int(terminal["output_seq"] or 0))
            norm_status = str(status or "").strip().lower()
            if norm_status in {"stopped", "failed"}:
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_status = ?,
                        owner_mode = 'managed',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (norm_status, _now(), terminal["session_id"]),
                )
            elif norm_status in {"attached", "running", "live", "idle", "starting", "stopping"}:
                # Mirror the live terminal status onto the session so the
                # status engine sees the console advance past "starting".
                # Without this agent_sessions.terminal_status stays "starting"
                # forever and the engine reports a permanent transitioning
                # "working" even for an idle console.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_status = ?,
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (norm_status, _now(), terminal["session_id"]),
                )
            await _invalidate_agent_live_state(db, terminal["agent_id"])
            await db.commit()
        finally:
            await db.close()
        # Ordered, post-commit, coalesced broadcast — the single source of
        # live terminal output for the dashboard. Best-effort.
        if self.ws_manager is not None and (output or norm_status):
            try:
                await self.ws_manager.broadcast(
                    "terminal_output",
                    {
                        "terminalId": terminal_id,
                        "agentId": str(terminal["agent_id"] or ""),
                        "status": norm_status,
                        "output": output or "",
                        "seq": seq or int(terminal["output_seq"] or 0),
                    },
                )
            except BaseException:
                pass

    async def flush_all(self) -> None:
        while True:
            async with self._lock:
                ids = list(self._pending.keys())
            if not ids:
                return
            for terminal_id in ids:
                await self.flush_terminal(terminal_id)


TERMINAL_OUTPUT_WRITES = TerminalOutputWriteQueue()


async def flush_terminal_output_writes_for_tests() -> None:
    await TERMINAL_OUTPUT_WRITES.flush_all()

async def _release_stale_console_owner_for_claim(db, owner_session, req: DispatchClaimRequest) -> Optional[dict[str, Any]]:
    terminal_id = str(owner_session["terminal_id"] or "").strip()
    terminal_status = str(owner_session["terminal_status"] or "").strip().lower()
    terminal = None
    if terminal_id:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if terminal:
            terminal_status = str(terminal["status"] or terminal_status or "").strip().lower()

    settings = await _load_settings(db)
    stale_after = max(30, int(settings.get("environment_offline_seconds", 90) or 90))
    updated_at = _iso_to_epoch((terminal["updated_at"] if terminal else "") or "")
    terminal_stale = bool(updated_at and (time.time() - updated_at) > stale_after)
    terminal_bridge_id = str((terminal["bridge_id"] if terminal else "") or "").strip()
    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (owner_session["environment_id"],))).fetchone()
    env_status = _environment_effective_status(env_row, offline_seconds=stale_after) if env_row else "offline"
    bridge_current = bool(
        env_row
        and env_status in {"online", "degraded"}
        and terminal_bridge_id
        and terminal_bridge_id == str(env_row["bridge_id"] or "").strip()
    )
    active_status = terminal_status in {"starting", "attached", "running", "active", "idle"}
    if terminal and active_status and bridge_current and not terminal_stale:
        return {
            "reason": "console_owner_active",
            "sessionId": owner_session["id"],
            "terminalId": terminal_id,
            "terminalStatus": terminal_status,
            "hint": "Console owns this runtime handle. Stop or return Console to managed before claiming managed Messenger work.",
        }

    now = _now()
    reason = "Released stale Console owner before managed dispatch claim."
    await db.execute(
        """
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            terminal_status = 'failed',
            last_seen = ?
        WHERE id = ?
        """,
        (now, owner_session["id"]),
    )
    if terminal:
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'failed',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_owner_released",
            json.dumps({
                "reason": "stale Console owner",
                "requestedByBridge": req.bridgeId or "",
                "previousBridge": terminal_bridge_id,
                "environmentBridge": str(env_row["bridge_id"] or "").strip() if env_row else "",
                "terminalStatus": terminal_status,
            }),
        )
    return None


async def _release_stale_terminal_owner(db, row, *, reason: str):
    terminal_id = str(row["terminal_id"] or "").strip()
    session_id = str(row["session_id"] or "").strip()
    if not terminal_id or not session_id:
        return
    now = _now()
    await db.execute(
        """
        UPDATE terminal_sessions
        SET status = 'failed',
            updated_at = ?,
            stopped_at = COALESCE(stopped_at, ?),
            error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
        WHERE id = ?
        """,
        (now, now, reason, terminal_id),
    )
    await db.execute(
        """
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            terminal_status = 'failed',
            last_seen = ?
        WHERE id = ?
          AND terminal_id = ?
        """,
        (now, session_id, terminal_id),
    )
    await _append_terminal_event(
        db,
        terminal_id,
        "terminal_owner_released",
        json.dumps({"reason": reason}),
    )


async def _active_terminal_for_agent(db, agent_id: str, *, settings: Optional[dict[str, Any]] = None):
    row = await (await db.execute(
        """
        SELECT
            s.id AS session_id,
            s.environment_id AS session_environment_id,
            s.owner_mode,
            s.terminal_status,
            s.runtime AS session_runtime,
            t.id AS terminal_id,
            t.environment_id,
            t.bridge_id,
            t.runtime,
            t.workspace,
            t.command,
            t.status,
            t.updated_at
        FROM agent_sessions s
        JOIN terminal_sessions t ON t.id = s.terminal_id
        WHERE s.agent_id = ?
          AND COALESCE(s.terminal_id, '') != ''
        ORDER BY s.last_seen DESC
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if not row:
        return None

    status = str(row["status"] or row["terminal_status"] or "").strip().lower()
    if status not in {"starting", "attached", "running", "active", "idle"}:
        return None
    runtime = _normalize_runtime(row["runtime"] or row["session_runtime"] or "")
    command = str(row["command"] or "").strip()
    if runtime == "claude-code" and command and "claude-aify" not in command:
        await _release_stale_terminal_owner(
            db,
            row,
            reason="Released legacy raw Claude terminal before managed channel dispatch; Claude backing must start through claude-aify.",
        )
        return None

    settings = settings or await _load_settings(db)
    stale_after = max(30, int(settings.get("environment_offline_seconds", 90) or 90))
    updated_at = _iso_to_epoch(row["updated_at"] or "")
    if updated_at and (time.time() - updated_at) > stale_after:
        await _release_stale_terminal_owner(db, row, reason="Released stale Console owner before managed PTY dispatch.")
        return None

    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (row["environment_id"],))).fetchone()
    env_status = _environment_effective_status(env_row, offline_seconds=stale_after) if env_row else "offline"
    if env_status not in {"online", "degraded"}:
        await _release_stale_terminal_owner(db, row, reason="Released unavailable Console owner before managed PTY dispatch.")
        return None
    if str(row["bridge_id"] or "").strip() != str(env_row["bridge_id"] or "").strip():
        await _release_stale_terminal_owner(db, row, reason="Released stale Console owner before managed PTY dispatch.")
        return None
    return row


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


async def _has_claimable_steerable_run(
    db,
    *,
    agent_row,
    supported_modes: set[str],
    agent_runtime: str,
) -> bool:
    """True when the turn-busy claim gate should be BYPASSED because a queued
    channel/resident run can be steered (injected) into a mid-turn target.

    Used only by the /dispatch/claim turn-busy gate (send-deadlock fix,
    2026-06-02). The carve-out fires when BOTH hold:

      * the TARGET can accept a mid-turn inject — `steer` is in its computed
        capabilities (_row_capabilities). For claude that means a managed or
        channelEnabled-resident session; a plain resident claude without
        channelEnabled, or a resident codex/opencode/pi, has no `steer` and is
        NOT bypassed. This is the SAME predicate the send-time steer path uses
        (line ~6770: `active_run and "steer" in capabilities`), so the gate and
        the steer route agree on who is injectable.
      * there is at least one QUEUED run in channel/resident execution mode that
        this bridge's supported_modes can actually claim. A managed (headless)
        run is never injectable, so it stays queued behind the turn as before.

    Returning False preserves the original "wait for the turn to end" behavior.
    """
    capabilities = _row_capabilities(agent_row)
    if "steer" not in capabilities:
        return False
    target_agent = str((agent_row["id"] if agent_row else "") or "")
    if not target_agent:
        return False
    cursor = await db.execute(
        """
        SELECT execution_mode, requested_runtime
        FROM dispatch_runs
        WHERE target_agent = ? AND status = 'queued'
        ORDER BY requested_at ASC
        LIMIT 25
        """,
        (target_agent,),
    )
    for run in await cursor.fetchall():
        run_execution_mode = str((run["execution_mode"] or "managed")).strip().lower()
        if run_execution_mode not in {"channel", "resident"}:
            continue
        if supported_modes and run_execution_mode not in supported_modes:
            continue
        requested_runtime = str(run["requested_runtime"] or "").strip()
        if requested_runtime and _normalize_runtime(requested_runtime) != agent_runtime:
            continue
        return True
    return False


async def _select_online_environment_for_runtime(
    db, runtime: str, *, offline_seconds: int = 90
) -> Optional[dict[str, Any]]:
    """Pick the freshest ONLINE environment that advertises `runtime`.

    Used by Phase 2 auto-bind: when a managed agent has no usable session
    environment, bind it to a live env so it can be cold-started on first
    message. Deterministic order: most-recently-seen environment first, so a
    freshly-heartbeating bridge is preferred. Returns the environment dict, or
    None when no online environment advertises the runtime.
    """
    normalized_runtime = _normalize_runtime(runtime or "")
    if not normalized_runtime:
        return None
    cursor = await db.execute("SELECT * FROM environments ORDER BY last_seen DESC")
    for env_row in await cursor.fetchall():
        environment = _environment_record_to_dict(env_row, offline_seconds=offline_seconds)
        if str(environment.get("status") or "").lower() != "online":
            continue
        if not _runtime_capability_for_environment(environment, normalized_runtime):
            continue
        return environment
    return None


async def _coldstart_spawn_request_for_dispatch(
    db,
    agent_id: str,
    *,
    runtime: str,
    settings: dict[str, Any],
    requested_by: str,
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
    """
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        return False

    # Don't pile up duplicate cold-starts — a queued/claimed spawn_request is
    # already a claimable backing for this agent.
    existing = await (await db.execute(
        """
        SELECT id
        FROM spawn_requests
        WHERE agent_id = ?
          AND status IN ('queued', 'claimed')
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if existing:
        return False

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
            return False

    environment_id = str(environment.get("id") or "").strip()
    if not environment_id:
        return False

    workspace, workspace_root = _workspace_for_environment(environment, None, fallback_workspace)

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
            resume_policy, status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            now,
            now,
        ),
    )
    return True


async def _ensure_managed_pty_for_dispatch(db, agent_id: str, *, runtime: str, settings: dict[str, Any], requested_by: str):
    active = await _active_terminal_for_agent(db, agent_id, settings=settings)
    if active:
        return active
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        return None

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
    if normalized_runtime == "claude-code" and bool(settings.get("console_auto_confirm_claude_dev_channels")):
        await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=session["environment_id"],
            bridge_id=bridge_id,
            action="input",
            requested_by=requested_by or "dashboard",
            body="\r",
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "managed_pty_channel_prompt_confirm_requested",
            json.dumps({"requestedBy": requested_by or "dashboard", "reason": "confirm Claude development channel prompt"}),
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
            last_seen = ?
        WHERE id = ?
        """,
        (bridge_id, terminal_id, command, workspace, now, session["id"]),
    )
    return await _active_terminal_for_agent(db, agent_id, settings=settings)


def _console_dispatch_input_body(req: DispatchRequest, *, recipient_id: str, message_id: str, bracketed_paste: bool = True) -> str:
    subject = str(req.subject or "").strip()
    body = str(req.body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    message = "\n".join(
        part for part in [
            "AIFY dashboard message",
            f"From: {req.from_agent}",
            f"To: {recipient_id}",
            f"Type: {req.type}",
            f"Subject: {subject}" if subject else "",
            f"MessageId: {message_id}",
            "",
            body,
            "",
            "Reply in the dashboard when appropriate, using the available aify-comms tools.",
        ] if part != ""
    )
    if bracketed_paste:
        return f"\x1b[200~{message}\x1b[201~\r"
    return f"{message}\r"


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


async def _record_terminal_delivery_contract(
    db,
    *,
    source_message_id: str,
    from_agent: str,
    recipient_id: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    in_reply_to: Optional[str],
    require_reply: bool,
    terminal_id: str,
    control_id: str,
    runtime: str = "",
) -> str:
    run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    requested_at = _now()
    normalized_runtime = _normalize_runtime(runtime or "")
    existing_active_turn = None
    if normalized_runtime in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        active_cursor = await db.execute(
            """
            SELECT id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND dispatch_mode = 'terminal'
              AND execution_mode = 'managed'
              AND runtime = ?
              AND status IN ('claimed', 'running')
            ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
            LIMIT 1
            """,
            (recipient_id, normalized_runtime),
        )
        existing_active_turn = await active_cursor.fetchone()
    if existing_active_turn:
        parent_run_id = str(existing_active_turn["id"] or "").strip()
        await _append_dispatch_event(
            db,
            parent_run_id,
            "terminal_delivered",
            f"Additional dashboard input delivered into terminal {terminal_id} with control {control_id}",
        )
        await _append_dispatch_event(
            db,
            parent_run_id,
            "terminal_coalesced",
            f"Coalesced message {source_message_id or 'unknown'} into active terminal-backed turn",
        )
        if source_message_id:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (source_message_id, recipient_id, requested_at),
            )
        await _invalidate_agent_live_state(db, recipient_id)
        return parent_run_id

    tracks_active_turn = normalized_runtime in {"claude-code", "codex", "hermes", "opencode", "pi"}
    status = "running" if tracks_active_turn else "delivered"
    await db.execute(
        """
        INSERT INTO dispatch_runs (
            id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime, runtime,
            message_type, subject, body, priority, in_reply_to, status, require_reply, requested_at, started_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            source_message_id or None,
            from_agent,
            recipient_id,
            "terminal",
            "managed",
            "",
            normalized_runtime,
            message_type,
            subject,
            body,
            priority,
            in_reply_to,
            status,
            1 if require_reply else 0,
            requested_at,
            requested_at if tracks_active_turn else None,
        ),
    )
    await _append_dispatch_event(
        db,
        run_id,
        "terminal_delivered",
        f"Delivered into terminal {terminal_id} with control {control_id}",
    )
    if tracks_active_turn:
        await _append_dispatch_event(
            db,
            run_id,
            "running",
            "Awaiting explicit reply from terminal-backed turn",
        )
    if source_message_id:
        await db.execute(
            "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (source_message_id, recipient_id, requested_at),
        )
    await _invalidate_agent_live_state(db, recipient_id)
    return run_id


_PRIORITY_ORDER = {"normal": 0, "high": 1, "urgent": 2}
_MERGED_DISPATCH_HEADER = "[AIFY PENDING DISPATCHES]"
_MERGED_DISPATCH_FOOTER = "[/AIFY PENDING DISPATCHES]"
_DISPATCH_BUFFER_CAP = 10
_CHANNEL_FANOUT_DEDUP_WINDOW_MS = 30_000


def _stronger_priority(left: str, right: str) -> str:
    left_key = str(left or "normal").strip().lower() or "normal"
    right_key = str(right or "normal").strip().lower() or "normal"
    return left_key if _PRIORITY_ORDER.get(left_key, 0) >= _PRIORITY_ORDER.get(right_key, 0) else right_key


def _clip_text(text: str, limit: int = 240) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)].rstrip() + "…"


def _render_pending_dispatch_item(
    index: int,
    *,
    from_agent: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    message_id: str = "",
    in_reply_to: str = "",
    requested_at: str = "",
) -> str:
    lines = [
        f"=== ITEM {index} ===",
        f"From: {from_agent or 'unknown'}",
        f"Type: {message_type or 'request'}",
        f"Subject: {subject or '(no subject)'}",
        f"Priority: {priority or 'normal'}",
    ]
    if requested_at:
        lines.append(f"At: {requested_at}")
    if message_id:
        lines.append(f"MessageId: {message_id}")
        lines.append("Full details are in the inbox. Read them there if you need the complete context.")
        preview = _clip_text(body or "", 240)
        if preview:
            lines.extend(["Body preview:", preview])
    else:
        if in_reply_to:
            lines.append(f"InReplyTo: {in_reply_to}")
        lines.extend(["Body:", str(body or "").strip()])
    return "\n".join(lines).strip()


def _pending_dispatch_count(body: str) -> int:
    text = str(body or "")
    if text.startswith(_MERGED_DISPATCH_HEADER):
        return len(re.findall(r"^=== ITEM \d+ ===$", text, flags=re.MULTILINE))
    return 1 if text.strip() else 0


def _build_pending_dispatch_subject(count: int, latest_subject: str) -> str:
    latest = _clip_text(latest_subject or "(no subject)", 80)
    if count <= 1:
        return latest
    return f"Pending updates ({count}); latest: {latest}"


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


async def _repair_unusable_active_runs(db, *, limit: int = 100) -> int:
    cursor = await db.execute(
        """
        SELECT *
        FROM dispatch_runs
        WHERE status IN ('claimed', 'running')
        ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
        LIMIT ?
        """,
        (max(1, int(limit or 100)),),
    )
    repaired = 0
    for row in await cursor.fetchall():
        state = await _get_dispatch_state_for_agent(db, row["target_agent"])
        active = state.get("activeRun")
        if not active or active.get("runId") != row["id"]:
            continue
        if await _link_unthreaded_completion_message_for_run(db, row):
            repaired += 1
            continue
        if await _close_idle_claude_terminal_run_without_reply(db, row):
            repaired += 1
            continue
        if await _close_idle_pi_terminal_run_without_reply(db, row):
            repaired += 1
            continue
        if await _discard_unusable_active_run(db, row["target_agent"], active):
            repaired += 1
    return repaired


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
    require_reply: bool = False,
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
            if active_run and "steer" in capabilities:
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
                    recipient_status = await _compute_agent_status(
                        recipient_row,
                        settings.get("idle_minutes", 5),
                        settings.get("offline_minutes", 30),
                        db,
                    )
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
            await db.execute(
                """
                UPDATE dispatch_runs
                SET subject = ?, body = ?, priority = ?, dispatch_mode = ?, message_type = ?, require_reply = ?
                WHERE id = ?
                """,
                (
                    _build_pending_dispatch_subject(merged_count, subject),
                    merged_body,
                    _stronger_priority(mergeable_run["priority"], priority),
                    "require_start" if mergeable_run["dispatch_mode"] == "require_start" or dispatch_mode == "require_start" else mergeable_run["dispatch_mode"],
                    message_type,
                    1 if (bool(mergeable_run["require_reply"]) or require_reply) else 0,
                    mergeable_run["id"],
                ),
            )
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

        run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime,
                message_type, subject, body, priority, in_reply_to, status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, source_message_id or None, from_agent, recipient_id, dispatch_mode, execution_mode, requested_runtime or "",
                message_type, subject, body, priority, in_reply_to, "queued", 1 if require_reply else 0, requested_at
            )
        )
        await _append_dispatch_event(db, run_id, "queued", f"{message_type}: {subject}")
        runs.append({"runId": run_id, "targetAgentId": recipient_id, "status": "queued", "requireReply": require_reply})
    return runs


async def _resolve_reply_parent_message_id(db, reply_id: Optional[str]) -> tuple[Optional[str], bool]:
    candidate = str(reply_id or "").strip()
    if not candidate:
        return None, True

    cursor = await db.execute("SELECT id FROM messages WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    if row:
        return candidate, True

    cursor = await db.execute("SELECT message_id FROM dispatch_runs WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    resolved = str((row["message_id"] if row else "") or "").strip()
    if resolved:
        return resolved, True

    return None, False


def _primary_result_message_id(message_id: str, recipients: list[str]) -> str:
    if len(recipients) == 1:
        return message_id
    if not recipients:
        return message_id
    return f"{message_id}-{recipients[0]}"


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


async def _dispatch_conversation_context(db, row, *, limit: int = 8) -> list[dict[str, Any]]:
    from_agent = str((row["from_agent"] if row else "") or "").strip()
    target_agent = str((row["target_agent"] if row else "") or "").strip()
    if not from_agent or not target_agent:
        return []
    current_message_ids = set(_dispatch_source_message_ids(row))
    cursor = await db.execute(
        """
        SELECT id, from_agent, to_agent, type, subject, body, priority, timestamp, in_reply_to
        FROM messages
        WHERE source = 'direct'
          AND (
            (from_agent = ? AND to_agent = ?)
            OR (from_agent = ? AND to_agent = ?)
          )
        ORDER BY timestamp DESC, rowid DESC
        LIMIT ?
        """,
        (from_agent, target_agent, target_agent, from_agent, max(1, int(limit or 8)) + len(current_message_ids)),
    )
    rows = await cursor.fetchall()
    context = []
    for message in reversed(rows):
        if message["id"] in current_message_ids:
            continue
        context.append({
            "id": message["id"],
            "from": message["from_agent"],
            "to": message["to_agent"],
            "type": message["type"],
            "subject": message["subject"],
            "body": message["body"] or "",
            "priority": message["priority"],
            "timestamp": message["timestamp"],
            "inReplyTo": message["in_reply_to"],
        })
        if len(context) >= limit:
            break
    return context


def _serialize_inbox_message(row, *, include_body: bool) -> dict[str, Any]:
    msg = {
        "id": row["id"],
        "from": row["from_agent"],
        "type": row["type"],
        "source": row["source"],
        "channel": row["channel"],
        "subject": row["subject"],
        "preview": _clip_text(row["body"] or "", 240),
        "priority": row["priority"],
        "timestamp": row["timestamp"],
        "inReplyTo": row["in_reply_to"],
        "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
        "read": row["read_at"] is not None,
        "readAt": row["read_at"],
    }
    if include_body:
        msg["body"] = row["body"]
    if row["in_reply_to"]:
        msg["parentContext"] = None
    return msg


def _is_replaceable_auto_handoff_message(existing_message, replied_run) -> bool:
    if not existing_message or not replied_run:
        return True
    existing_body = str((existing_message["body"] if "body" in existing_message.keys() else "") or "")
    if existing_body.startswith("Auto-mirrored dispatch "):
        return True
    return (
        existing_body == _auto_handoff_body_for_run(replied_run)
        and str((existing_message["subject"] if "subject" in existing_message.keys() else "") or "").strip()
        == _auto_handoff_subject_for_run(replied_run)
        and str((existing_message["from_agent"] if "from_agent" in existing_message.keys() else "") or "").strip()
        == str((replied_run["target_agent"] if "target_agent" in replied_run.keys() else "") or "").strip()
        and str((existing_message["to_agent"] if "to_agent" in existing_message.keys() else "") or "").strip()
        == str((replied_run["from_agent"] if "from_agent" in replied_run.keys() else "") or "").strip()
        and str((existing_message["in_reply_to"] if "in_reply_to" in existing_message.keys() else "") or "").strip()
        == str((replied_run["message_id"] if "message_id" in replied_run.keys() else "") or "").strip()
    )


_HANDOFF_REPLY_TYPES = {"response", "review", "error", "approval"}
_COMPLETION_INFO_RE = re.compile(
    r"\b(done|complete(?:d)?|finished|fixed|pushed|committed|shipped|merged|resolved|verified|ready|answered)\b",
    re.I,
)


def _message_satisfies_reply_contract(reply_type: str, subject: str = "", body: str = "") -> bool:
    msg_type = str(reply_type or "").strip().lower()
    if msg_type in _HANDOFF_REPLY_TYPES:
        return True
    # `info` closes a run ONLY when it signals completion (keyword) — an agent
    # may thread an `info` "ack / I'm looking" WITHOUT claiming the work is done,
    # which intentionally leaves the run open (see
    # test_threaded_non_answer_message_does_not_close_reply_contract). Reviewed
    # 2026-05-31 (holistic review "F4"): this is deliberate, NOT a stuck-run bug —
    # the operator-observed "Pending updates (N)" pile-up was QUEUED (never
    # claimed) runs, fixed by the release + channel-sidecar self-heal fixes.
    if msg_type == "info" and _COMPLETION_INFO_RE.search(f"{subject or ''}\n{body or ''}"):
        return True
    return False


async def _clear_turn_busy_if_no_open_reply_owing_run(db, target_agent: str, exclude_run_id: str) -> bool:
    """Clear turn_busy for a channel/resident target ONLY when no OTHER
    require_reply=1 channel/resident run is still open for it.

    Event-based working-state clear shared by two completion paths:

      * a reply landing for an rr=1 run (_mark_dispatch_run_answered); and
      * an rr=0 channel/resident delivery being marked completed by the bridge
        (PATCH /dispatch/runs) — an info/response wake is NOT sustained work, so
        leaving turn_busy stamped from its delivery re-pulse (claude-channel.js
        re-pulses turn_busy on every delivery) was the send-deadlock: the next
        queued send saw a fresh phantom turn_busy and waited out the 120s window.

    The "no other open rr=1 run" guard is the anti-feedback-loop safety: we only
    clear when the agent is NOT owing a reply on some other in-flight turn, so we
    never race a legitimate, still-running reply turn to 0. We never RE-ARM
    turn_busy here (anti-loop invariant) — only ever clear it.
    """
    if not target_agent:
        return False
    remaining_cursor = await db.execute(
        """
        SELECT COUNT(*) AS open_count
        FROM dispatch_runs
        WHERE target_agent = ?
          AND id != ?
          AND status IN ('claimed', 'running', 'delivered')
          AND execution_mode IN ('channel', 'resident')
          AND COALESCE(require_reply, 0) = 1
        """,
        (target_agent, exclude_run_id),
    )
    remaining = await remaining_cursor.fetchone()
    if not remaining or int(remaining["open_count"] or 0) != 0:
        return False
    await db.execute(
        """
        INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
        VALUES (?, 0, '', '', '', ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            turn_busy = 0,
            turn_run_id = '',
            turn_bridge_id = '',
            turn_runtime = '',
            turn_updated_at = excluded.turn_updated_at
        """,
        (target_agent, _now()),
    )
    return True


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
        status == "delivered"
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


async def _link_reply_message_to_dispatch_run(
    db,
    *,
    from_agent: str,
    resolved_in_reply_to: str,
    reply_message_id: str,
    reply_type: str,
    reply_body: str,
) -> bool:
    if not _message_satisfies_reply_contract(reply_type, body=reply_body):
        return False
    run_cursor = await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE target_agent = ? AND message_id = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (from_agent, resolved_in_reply_to),
    )
    replied_run = await run_cursor.fetchone()
    if not replied_run:
        return False
    existing_result_id = str(replied_run["result_message_id"] or "").strip()
    if existing_result_id:
        existing_cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (existing_result_id,))
        existing_message = await existing_cursor.fetchone()
        if not _is_replaceable_auto_handoff_message(existing_message, replied_run):
            return False

    current_status = str(replied_run["status"] or "").strip().lower()
    await _mark_dispatch_run_answered(
        db,
        replied_run["id"],
        reply_message_id,
        current_status,
        str(replied_run["execution_mode"] or ""),
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at)
        SELECT id, to_agent, ?
        FROM messages
        WHERE from_agent = ?
          AND to_agent = ?
          AND in_reply_to = ?
          AND dispatch_requested = 0
          AND body LIKE 'Auto-mirrored dispatch %'
        """,
        (_now(), from_agent, replied_run["from_agent"], replied_run["message_id"]),
    )
    handoff_note = (
        f"Result reply linked after run completion from {from_agent}"
        if current_status in _DISPATCH_TERMINAL_STATUSES
        else f"Result reply recorded from {from_agent}"
    )
    await _append_dispatch_event(db, replied_run["id"], "handoff", handoff_note)
    return True


_UNTHREADED_HANDOFF_WINDOW_MS = 24 * 60 * 60 * 1000


async def _close_steered_contracts_for_parent_run(
    db,
    parent_row,
    *,
    result_message_id: str,
) -> int:
    """Close same-sender steer contracts that were injected into a terminal run.

    Steer controls are extra guidance for the active turn. If the active turn's
    final result answers the same sender, that result also satisfies same-sender
    steered contracts that were delivered into the run.
    """
    result_message_id = str(result_message_id or "").strip()
    if not parent_row or not result_message_id:
        return 0
    parent_run_id = str(parent_row["id"] or "").strip()
    from_agent = str(parent_row["from_agent"] or "").strip()
    target_agent = str(parent_row["target_agent"] or "").strip()
    if not parent_run_id or not from_agent or not target_agent:
        return 0

    cursor = await db.execute(
        """
        SELECT r.id
        FROM dispatch_runs r
        JOIN dispatch_controls c ON c.source_message_id = r.message_id
        WHERE c.run_id = ?
          AND r.dispatch_mode = 'steer'
          AND r.status = 'delivered'
          AND r.from_agent = ?
          AND r.target_agent = ?
          AND COALESCE(r.result_message_id, '') = ''
        """,
        (parent_run_id, from_agent, target_agent),
    )
    rows = await cursor.fetchall()
    closed = 0
    for row in rows:
        await _mark_dispatch_run_answered(db, row["id"], result_message_id, "delivered")
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Closed by parent run {parent_run_id} result {result_message_id}",
        )
        closed += 1
    if closed:
        await _append_dispatch_event(
            db,
            parent_run_id,
            "handoff",
            f"Closed {closed} same-sender steered contract(s) with result {result_message_id}",
        )
    return closed


async def _link_unthreaded_reply_to_recent_dispatch_run(
    db,
    *,
    from_agent: str,
    to_agent: str,
    reply_message_id: str,
    reply_type: str,
    reply_subject: str = "",
    reply_body: str = "",
    reply_timestamp_ms: int,
) -> bool:
    if not _message_satisfies_reply_contract(reply_type, subject=reply_subject, body=reply_body):
        return False
    if not from_agent or not to_agent or not reply_message_id:
        return False

    latest_requested_at = _iso_from_ms(reply_timestamp_ms)
    earliest_requested_at = _iso_from_ms(max(0, reply_timestamp_ms - _UNTHREADED_HANDOFF_WINDOW_MS))
    run_cursor = await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE target_agent = ?
          AND from_agent = ?
          AND status IN ('delivered', 'claimed', 'running', 'completed', 'failed', 'cancelled')
          AND requested_at >= ?
          AND requested_at <= ?
          AND (
            require_reply = 1
            OR (
              dispatch_mode = 'terminal'
              AND runtime = 'claude-code'
              AND status IN ('claimed', 'running')
            )
          )
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (from_agent, to_agent, earliest_requested_at, latest_requested_at),
    )
    replied_run = await run_cursor.fetchone()
    if not replied_run:
        return False
    existing_result_id = str(replied_run["result_message_id"] or "").strip()
    if existing_result_id:
        existing_cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (existing_result_id,))
        existing_message = await existing_cursor.fetchone()
        if not _is_replaceable_auto_handoff_message(existing_message, replied_run):
            return False

    await _mark_dispatch_run_answered(
        db,
        replied_run["id"],
        reply_message_id,
        str(replied_run["status"] or ""),
        str(replied_run["execution_mode"] or ""),
    )
    await _append_dispatch_event(
        db,
        replied_run["id"],
        "handoff",
        f"Unthreaded result reply linked from {from_agent}",
    )
    return True


async def _link_unthreaded_completion_message_for_run(db, row) -> bool:
    if not row:
        return False
    is_active_claude_terminal_turn = (
        str((row["dispatch_mode"] if "dispatch_mode" in row.keys() else "") or "").strip().lower() == "terminal"
        and _normalize_runtime(str((row["runtime"] if "runtime" in row.keys() else "") or "")) == "claude-code"
        and str((row["status"] if "status" in row.keys() else "") or "").strip().lower() in {"claimed", "running"}
    )
    if not bool(int((row["require_reply"] if "require_reply" in row.keys() else 0) or 0)) and not is_active_claude_terminal_turn:
        return False
    if str((row["result_message_id"] if "result_message_id" in row.keys() else "") or "").strip():
        return False
    from_agent = str(row["from_agent"] or "").strip()
    target_agent = str(row["target_agent"] or "").strip()
    if not from_agent or not target_agent:
        return False
    requested_ms = int(_iso_to_epoch(str(row["requested_at"] or "")) * 1000)
    if not requested_ms:
        return False
    cursor = await db.execute(
        """
        SELECT id, type, subject, body, timestamp
        FROM messages
        WHERE from_agent = ?
          AND to_agent = ?
          AND source = 'direct'
          AND COALESCE(in_reply_to, '') = ''
          AND timestamp >= ?
        ORDER BY timestamp ASC, id ASC
        LIMIT 50
        """,
        (target_agent, from_agent, requested_ms),
    )
    for message in await cursor.fetchall():
        if not _message_satisfies_reply_contract(message["type"], subject=message["subject"], body=message["body"]):
            continue
        await _mark_dispatch_run_answered(
            db,
            row["id"],
            message["id"],
            str(row["status"] or ""),
            str(row["execution_mode"] or ""),
        )
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Unthreaded completion message {message['id']} linked during reconcile",
        )
        return True
    return False


def _auto_handoff_subject_for_run(row) -> str:
    subject = str((row["subject"] if row else "") or (row["id"] if row else "") or "dispatch result").strip()
    status = str((row["status"] if row else "") or "").strip().lower()
    if status == "failed":
        return f"[FAILED] {subject}"
    if status == "cancelled":
        return f"[CANCELLED] {subject}"
    return f"Re: {subject}"


def _auto_handoff_body_for_run(row) -> str:
    status = str((row["status"] if row else "") or "").strip().lower()
    from_agent = str((row["from_agent"] if row else "") or "").strip()
    if status == "failed":
        detail = str((row["error_text"] if row else "") or (row["summary"] if row else "") or "Run failed.").strip()
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


async def _mirror_dashboard_run_summary_to_chat(db, row) -> Optional[str]:
    """Persist dashboard-started managed run final text as a chat reply.

    Work Loop reply debt and operator-visible chat delivery are separate
    concerns. Routine dashboard `info` asks should not become contracts, but
    their managed runtime final text still needs to land in dashboard chat.
    """
    if not row:
        return None
    if str((row["from_agent"] if "from_agent" in row.keys() else "") or "").strip() != "dashboard":
        return None
    if str((row["status"] if "status" in row.keys() else "") or "").strip().lower() != "completed":
        return None
    if str((row["result_message_id"] if "result_message_id" in row.keys() else "") or "").strip():
        return None
    if _is_delivery_only_claude_run(row):
        return None
    current_cursor = await db.execute("SELECT result_message_id FROM dispatch_runs WHERE id = ?", (row["id"],))
    current_row = await current_cursor.fetchone()
    if str((current_row["result_message_id"] if current_row else "") or "").strip():
        return None

    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    target_agent = str((row["target_agent"] if "target_agent" in row.keys() else "") or "").strip()
    if not summary or not target_agent:
        return None

    start_ms = int(
        _iso_to_epoch(
            (row["started_at"] if "started_at" in row.keys() else "")
            or (row["claimed_at"] if "claimed_at" in row.keys() else "")
            or (row["requested_at"] if "requested_at" in row.keys() else "")
        )
        * 1000
    )
    source_message_id = str((row["message_id"] if "message_id" in row.keys() else "") or "").strip()
    explicit_cursor = await db.execute(
        """
        SELECT id
        FROM messages
        WHERE from_agent = ?
          AND to_agent = 'dashboard'
          AND source = 'direct'
          AND timestamp >= ?
        ORDER BY timestamp ASC, id ASC
        LIMIT 1
        """,
        (target_agent, max(0, start_ms)),
    )
    explicit = await explicit_cursor.fetchone()
    if explicit:
        message_id = str(explicit["id"] or "").strip()
        await db.execute("UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?", (message_id, row["id"]))
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Linked existing dashboard reply {message_id}",
        )
        return message_id

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    subject = _auto_handoff_subject_for_run(row)
    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            target_agent,
            "dashboard",
            "direct",
            "response",
            subject,
            summary,
            row["priority"] or "normal",
            0,
            source_message_id or None,
            ts,
        ),
    )
    await db.execute("UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?", (message_id, row["id"]))
    await _append_dispatch_event(
        db,
        row["id"],
        "handoff",
        f"Stored dashboard-visible final reply as {message_id}",
    )
    return message_id


async def _maybe_report_async_manager_result_to_dashboard(db, row) -> Optional[str]:
    """Store manager/operator async run summaries in dashboard chat.

    The bridge already captures managed runtime final text as the run summary.
    Older running agents may not have the latest prompt/skill telling them to
    call comms_send(to="dashboard") after teammate replies arrive, so make the
    operator-visible report a backend invariant for manager-style coordinators.
    """
    if not row:
        return None
    if _row_require_reply(row):
        return None
    if str((row["from_agent"] if "from_agent" in row.keys() else "") or "").strip() == "dashboard":
        return None
    if str((row["status"] if "status" in row.keys() else "") or "").strip().lower() != "completed":
        return None

    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    if not summary:
        return None

    target_agent = str((row["target_agent"] if "target_agent" in row.keys() else "") or "").strip()
    if not target_agent:
        return None

    event_cursor = await db.execute(
        "SELECT 1 FROM dispatch_events WHERE run_id = ? AND event_type = 'dashboard_report' LIMIT 1",
        (row["id"],),
    )
    if await event_cursor.fetchone():
        return None

    agent_cursor = await db.execute("SELECT role FROM agents WHERE id = ?", (target_agent,))
    agent_row = await agent_cursor.fetchone()
    role = str((agent_row["role"] if agent_row else "") or "").strip().lower()
    if role not in {"manager", "operator", "lead", "coordinator"}:
        return None

    start_ms = int(
        _iso_to_epoch(
            (row["started_at"] if "started_at" in row.keys() else "")
            or (row["claimed_at"] if "claimed_at" in row.keys() else "")
            or (row["requested_at"] if "requested_at" in row.keys() else "")
        )
        * 1000
    )
    source_message_id = str((row["message_id"] if "message_id" in row.keys() else "") or "").strip()
    if source_message_id:
        source_cursor = await db.execute("SELECT timestamp FROM messages WHERE id = ? LIMIT 1", (source_message_id,))
        source_row = await source_cursor.fetchone()
        if source_row:
            start_ms = max(start_ms, int(source_row["timestamp"] or 0))
    explicit_cursor = await db.execute(
        """
        SELECT 1
        FROM messages
        WHERE from_agent = ?
          AND to_agent = 'dashboard'
          AND source = 'direct'
          AND timestamp >= ?
        LIMIT 1
        """,
        (target_agent, max(0, start_ms)),
    )
    if await explicit_cursor.fetchone():
        await _append_dispatch_event(
            db,
            row["id"],
            "dashboard_report_skipped",
            "Skipped async dashboard summary mirror because an explicit dashboard message already exists for this run window.",
        )
        return None

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    subject = str((row["subject"] if "subject" in row.keys() else "") or "").strip()
    if subject and not subject.lower().startswith(("re:", "update:")):
        subject = f"Update: {subject}"
    elif not subject:
        subject = "Update from managed run"

    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            target_agent,
            "dashboard",
            "direct",
            "info",
            subject,
            summary,
            row["priority"] or "normal",
            0,
            row["message_id"],
            ts,
        ),
    )
    await _append_dispatch_event(
        db,
        row["id"],
        "dashboard_report",
        f"Stored async manager/operator report for dashboard as {message_id}",
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


async def _has_recent_direct_delivery_for_channel_fanout(
    db,
    *,
    from_agent: str,
    recipient_id: str,
    message_type: str,
    body: str,
    timestamp_ms: int,
) -> bool:
    lower_bound = int(timestamp_ms) - _CHANNEL_FANOUT_DEDUP_WINDOW_MS
    upper_bound = int(timestamp_ms) + _CHANNEL_FANOUT_DEDUP_WINDOW_MS
    cursor = await db.execute(
        """
        SELECT 1
        FROM messages
        WHERE from_agent = ?
          AND to_agent = ?
          AND source = 'direct'
          AND type = ?
          AND body = ?
          AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (from_agent, recipient_id, message_type, body, lower_bound, upper_bound),
    )
    return await cursor.fetchone() is not None

# ─── Root ────────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {
        "service": "aify-comms",
        "version": "4.0.0",
        "storage": "sqlite",
        "endpoints": {
            "agents": "/api/v1/agents",
            "environments": "/api/v1/environments",
            "spawnRequests": "/api/v1/spawn-requests",
            "sessions": "/api/v1/sessions",
            "messages": "/api/v1/messages",
            "dispatch": "/api/v1/dispatch",
            "shared": "/api/v1/shared",
            "channels": "/api/v1/channels",
            "settings": "/api/v1/settings",
            "dashboard": "/api/v1/dashboard",
            "stats": "/api/v1/stats",
        },
    }


# ─── Environments ────────────────────────────────────────────────────────────

@router.get("/environments")
async def list_environments(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM environments WHERE status != 'forgotten'")
        environments = [
            _environment_record_to_dict(row, offline_seconds=settings.get("environment_offline_seconds", 90))
            for row in await cursor.fetchall()
        ]
        status_rank = {"online": 0, "degraded": 1, "unknown": 2, "offline": 3, "disabled": 4}
        environments.sort(key=lambda env: (status_rank.get(env.get("status") or "", 5), str(env.get("label") or "").lower(), str(env.get("id") or "").lower()))
        return {"ok": True, "environments": environments}
    finally:
        await db.close()


@router.post("/environments/heartbeat")
async def environment_heartbeat(req: EnvironmentHeartbeat, request: Request):
    env_id = str(req.id or "").strip()
    if not env_id:
        raise HTTPException(400, "Environment id is required")

    now = _now()
    cwd_roots = _normalize_roots(req.cwdRoots or [])
    runtimes = req.runtimes or []
    metadata = req.metadata or {}
    if req.terminal is not None:
        metadata["terminal"] = bool(req.terminal)
    if req.pty is not None:
        metadata["pty"] = bool(req.pty)
    if req.terminalRuntimes is not None:
        metadata["terminalRuntimes"] = [
            _normalize_runtime(str(runtime or ""))
            for runtime in req.terminalRuntimes
            if str(runtime or "").strip()
        ]
    requested_status = str(req.status or "online").strip().lower()
    if requested_status not in {"online", "degraded", "offline"}:
        requested_status = "online"
    db = await get_db()
    try:
        existing_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        existing = await existing_cursor.fetchone()
        # Forget-tombstone guard (2026-06-03): a row in `forgotten` status is the
        # environment-level equivalent of an agent tombstone. A passive heartbeat
        # from a still-running aify-comms bridge that predates the forget MUST NOT
        # resurrect it (the old bug: the blind UPDATE below flipped status back to
        # 'online' seconds after the operator forgot the env). Only a genuine fresh
        # (re)launch — a bridge whose bridgeStartedAt is newer than forgottenAt —
        # is allowed to clear the tombstone and re-register. Mirrors how agent
        # registration honors agent_tombstones unless explicitly restored.
        if existing and str(existing["status"] or "").strip().lower() == "forgotten":
            forgotten_meta = _json_loads_or(existing["metadata"], {})
            forgotten_at = _timestamp_sort_key(forgotten_meta.get("forgottenAt"))
            incoming_started = _bridge_started_at(metadata)
            relaunched = bool(incoming_started) and (not forgotten_at or incoming_started > forgotten_at)
            if not relaunched:
                # Lingering/passive heartbeat — keep the env forgotten, do not touch
                # last_seen or status. Return the tombstoned record as-is.
                return {"ok": True, "environment": _environment_record_to_dict(existing), "forgotten": True}
        registered_at = existing["registered_at"] if existing else now
        existing_metadata = _json_loads_or(existing["metadata"], {}) if existing else {}
        manual_roots = bool(existing_metadata.get("manualRoots"))
        effective_roots = _json_loads_or(existing["cwd_roots"], []) if existing and manual_roots else cwd_roots
        next_metadata = {**metadata, "advertisedCwdRoots": cwd_roots}
        if manual_roots:
            next_metadata.update({
                "manualRoots": True,
                "manualRootsUpdatedAt": existing_metadata.get("manualRootsUpdatedAt", ""),
                "manualRootsUpdatedBy": existing_metadata.get("manualRootsUpdatedBy", ""),
            })
        superseded_bridge_id = ""
        if existing and str(existing["bridge_id"] or "").strip() and str(req.bridgeId or "").strip():
            existing_bridge_id = str(existing["bridge_id"] or "").strip()
            incoming_bridge_id = str(req.bridgeId or "").strip()
            if existing_bridge_id != incoming_bridge_id:
                existing_metadata = _json_loads_or(existing["metadata"], {})
                existing_started = _bridge_started_at(existing_metadata)
                incoming_started = _bridge_started_at(metadata)
                if existing_started and (not incoming_started or incoming_started < existing_started):
                    return {"ok": True, "environment": _environment_record_to_dict(existing)}
                if incoming_started and (not existing_started or incoming_started > existing_started):
                    superseded_bridge_id = existing_bridge_id
        if (
            existing
            and requested_status != "online"
            and str(existing["bridge_id"] or "").strip()
            and str(req.bridgeId or "").strip()
            and str(existing["bridge_id"] or "").strip() != str(req.bridgeId or "").strip()
        ):
            return {"ok": True, "environment": _environment_record_to_dict(existing)}
        if existing:
            await db.execute(
                """
                UPDATE environments
                SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?,
                    bridge_version = ?, cwd_roots = ?, runtimes = ?, status = ?,
                    metadata = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    req.label or env_id,
                    req.machineId or "",
                    req.os or "",
                    req.kind or "",
                    req.bridgeId or "",
                    req.bridgeVersion or "",
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    now,
                    env_id,
                ),
            )
        else:
            await db.execute(
                """
                INSERT INTO environments (
                    id, label, machine_id, os, kind, bridge_id, bridge_version,
                    cwd_roots, runtimes, status, metadata, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    env_id,
                    req.label or env_id,
                    req.machineId or "",
                    req.os or "",
                    req.kind or "",
                    req.bridgeId or "",
                    req.bridgeVersion or "",
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    registered_at,
                    now,
                ),
            )
        if superseded_bridge_id:
            pending_cursor = await db.execute(
                """
                SELECT id
                FROM environment_controls
                WHERE environment_id = ?
                  AND bridge_id = ?
                  AND action = 'stop'
                  AND status IN ('pending', 'claimed')
                LIMIT 1
                """,
                (env_id, superseded_bridge_id),
            )
            pending = await pending_cursor.fetchone()
            if not pending:
                await db.execute(
                    """
                    INSERT INTO environment_controls (
                        id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"envctl-{uuid.uuid4().hex}",
                        env_id,
                        superseded_bridge_id,
                        req.machineId or "",
                        "stop",
                        "pending",
                        "server:superseded-bridge",
                        now,
                    ),
                )
        # Env recovery / status transition: when the env flips between online and
        # offline/degraded, bound agents' derived status (offline ↔ available/online)
        # changes too. Invalidate their live-status cache so the transition shows
        # immediately rather than after the ~90s env window / 60s sweep.
        prior_status = str((existing["status"] if existing else "") or "").strip().lower()
        if existing and prior_status != requested_status:
            bound_rows = await (await db.execute(
                "SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?",
                (env_id,),
            )).fetchall()
            for bound in bound_rows:
                bound_agent = str(bound["agent_id"] or "").strip()
                if bound_agent:
                    await _invalidate_agent_live_state(db, bound_agent)
        await db.commit()
        row_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        row = await row_cursor.fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_heartbeat", {"environmentId": env_id, "bridgeId": req.bridgeId or ""})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()


@router.patch("/environments/{environment_id:path}/roots")
async def update_environment_roots(environment_id: str, req: EnvironmentRootsUpdate, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            raise HTTPException(404, "Environment not found")
        now = _now()
        metadata = _json_loads_or(env["metadata"], {})
        if req.resetToBridgeAdvertised:
            roots = _normalize_roots(metadata.get("advertisedCwdRoots") or _json_loads_or(env["cwd_roots"], []))
            next_metadata = {k: v for k, v in metadata.items() if k not in {"manualRoots", "manualRootsUpdatedAt", "manualRootsUpdatedBy"}}
            next_metadata["manualRoots"] = False
            next_metadata["manualRootsResetAt"] = now
            next_metadata["manualRootsResetBy"] = req.requestedBy or "dashboard"
        else:
            roots = _normalize_roots(req.roots or [])
            if not roots:
                raise HTTPException(400, "At least one root is required. Use resetToBridgeAdvertised to return to bridge-advertised roots.")
            next_metadata = {
                **metadata,
                "manualRoots": True,
                "manualRootsUpdatedAt": now,
                "manualRootsUpdatedBy": req.requestedBy or "dashboard",
                "previousCwdRoots": _json_loads_or(env["cwd_roots"], []),
            }
        await db.execute(
            """
            UPDATE environments
            SET cwd_roots = ?,
                metadata = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(roots), json.dumps(next_metadata), now, environment_id),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_roots_updated", {"environmentId": environment_id})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()


# ─── Spawn Requests And Sessions ─────────────────────────────────────────────

@router.get("/spawn-requests")
async def list_spawn_requests(
    request: Request,
    status: Optional[str] = None,
    environmentId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    db = await get_db()
    try:
        await _repair_spawn_requests_from_initial_dispatch_failures(db)
        where = []
        params: list[Any] = []
        if status:
            where.append("sr.status = ?")
            params.append(status)
        if environmentId:
            where.append("sr.environment_id = ?")
            params.append(environmentId)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"""
            SELECT sr.*, ss.id AS spec_row_id
            FROM spawn_requests sr
            LEFT JOIN spawn_specs ss ON ss.id = sr.spawn_spec_id
            {where_sql}
            ORDER BY sr.created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            spec_cursor = await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (row["spawn_spec_id"],))
            spec_row = await spec_cursor.fetchone()
            result.append(_spawn_request_to_dict(row, _spawn_spec_to_dict(spec_row) if spec_row else None))
        return {"ok": True, "spawnRequests": result}
    finally:
        await db.close()


@router.post("/environments/{environment_id:path}/control")
async def control_environment(environment_id: str, req: EnvironmentControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"stop", "forget"}:
        raise HTTPException(400, "Environment control action must be stop or forget")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            raise HTTPException(404, "Environment not found")
        now = _now()
        if action == "forget":
            await db.execute("DELETE FROM environment_controls WHERE environment_id = ?", (environment_id,))
            await db.execute(
                """
                UPDATE environments
                SET status = 'forgotten',
                    bridge_id = '',
                    bridge_version = '',
                    runtimes = '[]',
                    metadata = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (json.dumps({**_json_loads_or(env["metadata"], {}), "forgottenAt": now, "forgottenBy": req.requestedBy or "dashboard"}), now, environment_id),
            )
            await db.commit()
            ws = await _get_ws(request)
            if ws: await ws.broadcast("environment_forgotten", {"environmentId": environment_id})
            return {"ok": True, "action": action, "environmentId": environment_id}

        control_id = f"envctl-{uuid.uuid4().hex}"
        await db.execute(
            """
            INSERT INTO environment_controls (
                id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                control_id,
                environment_id,
                env["bridge_id"] or "",
                env["machine_id"] or "",
                action,
                "pending",
                req.requestedBy or "dashboard",
                now,
            ),
        )
        await db.execute("UPDATE environments SET status = ? WHERE id = ?", ("disabled", environment_id))
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'lost',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE environment_id = ?
              AND status IN ('starting', 'running', 'recovering', 'restarting')
            """,
            (now, now, environment_id),
        )
        await db.execute(
            """
            UPDATE agents
            SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                launch_mode = 'none',
                runtime_state = '{}',
                last_seen = ?
            WHERE id IN (SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?)
            """,
            (now, environment_id),
        )
        # `offline` is not a manual-status short-circuit, so the live-status cache
        # would otherwise keep serving the old status for these bound agents until
        # the 60s sweep. Invalidate each so the disable reflects immediately.
        bound_rows = await (await db.execute(
            "SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?",
            (environment_id,),
        )).fetchall()
        for bound in bound_rows:
            bound_agent = str(bound["agent_id"] or "").strip()
            if bound_agent:
                await _invalidate_agent_live_state(db, bound_agent)
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_requested", {"environmentId": environment_id, "action": action})
        return {"ok": True, "controlId": control_id, "action": action, "environmentId": environment_id}
    finally:
        await db.close()


@router.post("/environments/controls/claim")
async def claim_environment_control(req: EnvironmentControlClaim):
    db = await get_db()
    try:
        row = None
        while True:
            cursor = await db.execute(
                """
                SELECT *
                FROM environment_controls
                WHERE environment_id = ?
                  AND status = 'pending'
                  AND (bridge_id = '' OR bridge_id = ?)
                ORDER BY requested_at ASC
                LIMIT 1
                """,
                (req.environmentId, req.bridgeId),
            )
            candidate = await cursor.fetchone()
            if not candidate:
                return {"ok": True, "control": None}
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
            env = await env_cursor.fetchone()
            env_bridge_id = str((env["bridge_id"] if env else "") or "").strip()
            metadata = _json_loads_or(env["metadata"], {}) if env else {}
            bridge_started_at = metadata.get("bridgeStartedAt") or ""
            if (
                candidate["action"] == "stop"
                and env_bridge_id == req.bridgeId
                and _iso_to_epoch(candidate["requested_at"]) > 0
                and _iso_to_epoch(bridge_started_at) > 0
                and _iso_to_epoch(candidate["requested_at"]) < _iso_to_epoch(bridge_started_at)
            ):
                now = _now()
                await db.execute(
                    "UPDATE environment_controls SET status = 'failed', handled_at = ?, error = ? WHERE id = ? AND status = 'pending'",
                    (
                        now,
                        f'Stale stop control ignored because bridge "{req.bridgeId}" started after the control was requested.',
                        candidate["id"],
                    ),
                )
                await db.commit()
                continue
            row = candidate
            break
        now = _now()
        await db.execute(
            "UPDATE environment_controls SET status = 'claimed', machine_id = ?, claimed_at = ? WHERE id = ? AND status = 'pending'",
            (req.machineId or "", now, row["id"]),
        )
        await db.commit()
        return {
            "ok": True,
            "control": {
                "id": row["id"],
                "environmentId": row["environment_id"],
                "bridgeId": row["bridge_id"] or "",
                "action": row["action"],
                "requestedBy": row["requested_by"] or "",
                "requestedAt": row["requested_at"] or "",
                "currentEnvironment": _environment_record_to_dict(env) if env else None,
            },
        }
    finally:
        await db.close()


@router.patch("/environments/controls/{control_id}")
async def update_environment_control(control_id: str, req: EnvironmentControlUpdate, request: Request):
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, "Environment control status must be completed or failed")
    db = await get_db()
    try:
        now = _now()
        await db.execute(
            "UPDATE environment_controls SET status = ?, handled_at = ?, error = ? WHERE id = ?",
            (status, now, req.error or "", control_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_updated", {"controlId": control_id, "status": status})
        return {"ok": True, "controlId": control_id, "status": status}
    finally:
        await db.close()


@router.post("/spawn-requests")
async def create_spawn_request(req: SpawnRequestCreate, request: Request):
    validate_name(req.agentId, "agent ID")
    normalized_runtime = _normalize_runtime(req.runtime)
    mode = str(req.mode or "managed-warm").strip()
    if mode not in _SPAWN_MODES:
        raise HTTPException(400, f'Unsupported spawn mode "{mode}"')

    db = await get_db()
    try:
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{req.environmentId}" is {environment.get("status") or "unknown"}; restart its bridge before spawning.')
        runtime_capability = _runtime_capability_for_environment(environment, normalized_runtime)
        if not runtime_capability:
            raise HTTPException(400, f'Environment "{req.environmentId}" does not advertise runtime "{normalized_runtime}"')
        workspace = _normalize_workspace_for_environment(environment, req.workspace or "")
        workspace_root = _workspace_root_for(environment, workspace)
        if not workspace and workspace_root:
            workspace = workspace_root
        settings = await _load_settings(db)
        model = str(req.model or "").strip()
        if not model:
            if normalized_runtime == "codex":
                model = str(settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"])).strip()
            elif normalized_runtime == "claude-code":
                model = str(settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"])).strip()
            elif normalized_runtime == "pi":
                model = str(settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"])).strip()
        runtime_config = req.runtimeConfig or {}
        if normalized_runtime == "codex" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]).strip()}
        elif normalized_runtime == "claude-code" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]).strip()}
        elif normalized_runtime == "pi" and not str(runtime_config.get("effort") or runtime_config.get("thinking") or "").strip():
            pi_effort = str(settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]).strip()
            if pi_effort:
                runtime_config = {**runtime_config, "effort": pi_effort}
        metadata = req.metadata or {}
        if runtime_config:
            metadata = {**metadata, "runtimeConfig": runtime_config}

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
                req.agentId,
                req.environmentId,
                normalized_runtime,
                workspace,
                model,
                req.profile or "",
                mode,
                req.systemPrompt or "",
                req.instructions or "",
                json.dumps(req.envVars or {}),
                json.dumps(req.channelIds or []),
                json.dumps(req.budgetPolicy or {}),
                json.dumps(req.contextPolicy or {}),
                json.dumps(req.restartPolicy or {}),
                json.dumps(metadata),
                now,
                now,
            ),
        )
        await db.execute(
            """
            INSERT INTO spawn_requests (
                id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                workspace, workspace_root, initial_message, priority, subject, mode,
                resume_policy, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id,
                spec_id,
                req.createdBy or "dashboard",
                req.environmentId,
                req.agentId,
                req.role or "coder",
                req.name or req.agentId,
                normalized_runtime,
                workspace,
                workspace_root,
                req.initialMessage or "",
                req.priority or "normal",
                req.subject or "",
                mode,
                req.resumePolicy or "native_first",
                "queued",
                now,
                now,
            ),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
        spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_created", {"spawnRequestId": request_id, "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(row, _spawn_spec_to_dict(spec))}
    finally:
        await db.close()


@router.post("/spawn-requests/claim")
async def claim_spawn_request(req: SpawnRequestClaim, request: Request):
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            await db.rollback()
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        env_bridge_id = str(env_row["bridge_id"] or "").strip()
        if env_bridge_id and env_bridge_id != str(req.bridgeId or "").strip():
            await db.commit()
            return {
                "ok": True,
                "spawnRequest": None,
                "blockedBy": {
                    "reason": "bridge_not_current",
                    "environmentId": req.environmentId,
                    "bridgeId": req.bridgeId,
                    "currentBridgeId": env_bridge_id,
                },
            }

        row_cursor = await db.execute(
            """
            SELECT *
            FROM spawn_requests
            WHERE environment_id = ? AND status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (req.environmentId,),
        )
        row = await row_cursor.fetchone()
        if not row:
            await db.commit()
            return {"ok": True, "spawnRequest": None}

        claimed_at = _now()
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'claimed', claimed_by_bridge_id = ?, claim_machine_id = ?,
                claimed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (req.bridgeId, req.machineId or "", claimed_at, claimed_at, row["id"]),
        )
        await db.execute(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            (claimed_at, req.environmentId),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (row["id"],))).fetchone()
        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_claimed", {"spawnRequestId": row["id"], "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(spec_row) if spec_row else None)}
    finally:
        await db.close()


@router.patch("/spawn-requests/{spawn_request_id}")
async def update_spawn_request(spawn_request_id: str, req: SpawnRequestUpdate, request: Request):
    status_value = str(req.status or "").strip().lower()
    if status_value not in {"claimed", "starting", "running", "failed", "cancelled"}:
        raise HTTPException(400, f'Unsupported spawn request status "{req.status}"')
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f'Spawn request "{spawn_request_id}" not found')
        current_status = str(row["status"] or "").strip().lower()
        if current_status in {"failed", "cancelled"} and status_value != current_status:
            raise HTTPException(
                409,
                f'Spawn request "{spawn_request_id}" is already {current_status}; late bridge update "{status_value}" was ignored.',
            )
        if req.bridgeId and row["claimed_by_bridge_id"] and row["claimed_by_bridge_id"] != req.bridgeId:
            raise HTTPException(409, f'Spawn request "{spawn_request_id}" is claimed by another bridge')

        now = _now()
        session_id = row["session_id"] or ""
        finished_at = row["finished_at"]
        started_at = row["started_at"]
        if status_value == "starting" and not started_at:
            started_at = now
        if status_value in _SPAWN_TERMINAL_STATUSES:
            finished_at = now if status_value in {"failed", "cancelled"} else finished_at

        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (row["spawn_spec_id"],))).fetchone()
        if not spec_row:
            raise HTTPException(500, f'Spawn spec "{row["spawn_spec_id"]}" missing')

        runtime_state = req.runtimeState or {}
        if req.bridgeId:
            runtime_state = {**runtime_state, "bridgeInstanceId": req.bridgeId}

        if status_value == "running":
            session_id = session_id or f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            effective_session_handle = req.sessionHandle or row["session_handle"] or ""
            if effective_session_handle:
                runtime_state = _runtime_state_with_handle(row["runtime"], runtime_state, effective_session_handle)
            spec_metadata = _json_loads_or(spec_row["metadata"], {})
            runtime_config = spec_metadata.get("runtimeConfig") if isinstance(spec_metadata, dict) else {}
            if not isinstance(runtime_config, dict):
                runtime_config = {}
            agent_capabilities = _default_capabilities_for(row["runtime"], "managed", effective_session_handle, runtime_config)
            await db.execute(
                """
                INSERT INTO agents (
                    id, role, name, cwd, model, description, instructions, status, status_note,
                    runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                    capabilities, runtime_config, runtime_state, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    role = excluded.role,
                    name = excluded.name,
                    cwd = excluded.cwd,
                    model = excluded.model,
                    instructions = excluded.instructions,
                    status = excluded.status,
                    runtime = excluded.runtime,
                    machine_id = excluded.machine_id,
                    launch_mode = excluded.launch_mode,
                    session_mode = excluded.session_mode,
                    session_handle = excluded.session_handle,
                    managed_by = excluded.managed_by,
                    capabilities = excluded.capabilities,
                    runtime_config = excluded.runtime_config,
                    runtime_state = excluded.runtime_state,
                    last_seen = excluded.last_seen
                """,
                (
                    row["agent_id"],
                    row["role"] or "coder",
                    row["name"] or row["agent_id"],
                    row["workspace"] or "",
                    spec_row["model"] or "",
                    "",
                    spec_row["standing_instructions"] or "",
                    "idle",
                    "",
                    row["runtime"],
                    row["claim_machine_id"] or "",
                    "managed",
                    "managed",
                    effective_session_handle,
                    row["created_by"] or "dashboard",
                    json.dumps(agent_capabilities),
                    json.dumps(runtime_config),
                    json.dumps(runtime_state),
                    now,
                    now,
                ),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    req.bridgeId or row["claimed_by_bridge_id"] or "",
                    row["agent_id"],
                    row["claim_machine_id"] or "",
                    row["runtime"],
                    "managed",
                    now,
                    now,
                    "",
                    None,
                ),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode,
                    owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
                    process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    row["agent_id"],
                    row["environment_id"],
                    row["runtime"],
                    row["workspace"] or "",
                    row["mode"] or "managed-warm",
                    "managed",
                    req.bridgeId or row["claimed_by_bridge_id"] or "",
                    "",
                    "",
                    "",
                    "",
                    req.processId or "",
                    effective_session_handle,
                    "",
                    row["spawn_spec_id"],
                    row["id"],
                    json.dumps(req.capabilities or {"persistent": True, "bridgeResume": True}),
                    json.dumps(req.telemetry or {}),
                    "running",
                    started_at or now,
                    now,
                    None,
                ),
            )
            # Migrate a live terminal orphaned by this rotation (operator-reported
            # 2026-05-31, sc-architect). A managed respawn's bridge can create the
            # visible-TUI/console terminal a few seconds BEFORE this running
            # transition mints the new session, so the live terminal stays bound to
            # the prior (about-to-be-ended) session and the new running session gets
            # terminal_id=''. The dashboard then shows "Console not started" while
            # the real TUI is alive — and the live terminal row hangs off an ended
            # session, so the FK ON DELETE CASCADE could later drop a running TUI's
            # tracking. Re-point this agent's freshest LIVE, same-bridge terminal
            # onto the new session BEFORE ending the prior sessions.
            migrate_bridge_id = req.bridgeId or row["claimed_by_bridge_id"] or ""
            if migrate_bridge_id:
                live_terminal = await (await db.execute(
                    """
                    SELECT id, status, command, workspace, session_id FROM terminal_sessions
                    WHERE agent_id = ?
                      AND bridge_id = ?
                      AND id NOT LIKE 'vterm_%'
                      AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering')
                    ORDER BY datetime(COALESCE(updated_at, created_at, '1970-01-01')) DESC, rowid DESC
                    LIMIT 1
                    """,
                    (row["agent_id"], migrate_bridge_id),
                )).fetchone()
                if live_terminal and str(live_terminal["session_id"] or "") != session_id:
                    await db.execute(
                        "UPDATE terminal_sessions SET session_id = ? WHERE id = ?",
                        (session_id, live_terminal["id"]),
                    )
                    await db.execute(
                        """
                        UPDATE agent_sessions
                        SET terminal_id = ?, terminal_status = ?,
                            terminal_command = ?, terminal_workspace = ?
                        WHERE id = ?
                        """,
                        (
                            live_terminal["id"],
                            live_terminal["status"] or "",
                            live_terminal["command"] or "",
                            live_terminal["workspace"] or "",
                            session_id,
                        ),
                    )
            await db.execute(
                """
                UPDATE agent_sessions
                SET status = 'ended',
                    ended_at = COALESCE(NULLIF(ended_at, ''), ?),
                    last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
                WHERE agent_id = ?
                  AND id != ?
                  AND status IN ('starting', 'running', 'recovering', 'restarting')
                """,
                (now, now, row["agent_id"], session_id),
            )
            if row["status"] != "running" and str(row["initial_message"] or "").strip():
                runs = await _create_dispatch_runs(
                    db,
                    [row["agent_id"]],
                    from_agent=row["created_by"] or "dashboard",
                    message_type="request",
                    subject=row["subject"] or f"Spawn {row['agent_id']}",
                    body=row["initial_message"],
                    priority=row["priority"] or "normal",
                    in_reply_to=None,
                    dispatch_mode="start_if_possible",
                    execution_mode="managed",
                    requested_runtime=row["runtime"],
                    message_id=None,
                    require_reply=True,
                )
                # Spawn-time initial-message dispatches for managed claude
                # must honor insert_messages_via_console=false (the channel-
                # route default). Deep-test caught this earlier — without
                # the helper here e2e-test-claude's initial run stayed
                # execution_mode='managed' and claude-channel.js never
                # claimed it.
                settings_for_runs = await _load_settings(db)
                await _apply_channel_routing_to_claude_runs(db, runs, settings_for_runs)
                for run in runs:
                    _wake_agent(run["targetAgentId"])

            # Slices 1/2/4 (architectural): when managed_terminal_backing
            # is enabled, proactively launch the wrapper PTY for this
            # newly-registered managed agent. The wrapper stays alive
            # across dispatches; subsequent sends reuse it via slice 3's
            # console-attach reuse + the existing
            # _active_terminal_for_agent lookup in
            # _ensure_managed_pty_for_dispatch. Operator-visible win: no
            # "console pops up when I send" — the console pre-exists by
            # the time the first dispatch arrives. Best-effort: a
            # wrapper-launch failure here does NOT fail the spawn-request
            # running transition (the dispatch path's lazy spawn is the
            # fallback).
            settings_for_pty = await _load_settings(db)
            _is_claude_managed = _normalize_runtime(row["runtime"]) == "claude-code"
            _eager_flag = bool(settings_for_pty.get("managed_pty_eager_spawn", DEFAULT_SETTINGS["managed_pty_eager_spawn"]))
            # When insert_messages_via_console=false (the default), managed
            # claude needs a wrapper PTY hosting claude-aify so its
            # claude-channel.js child polls /dispatch/claim for this
            # specific agent. Without it, channel dispatches sit queued
            # forever (originally observed in run_1779309370301).
            _claude_needs_wrapper = _is_claude_managed and not _insert_messages_via_console(settings_for_pty)
            # Unified-backing refactor 2026-05-24: when this runtime is
            # wrapper-backed, the wrapper PTY MUST pre-exist by spawn-request
            # running transition — otherwise nothing claims dispatches (the
            # main bridge dispatch loop drops 'managed' from supportedExecutionModes
            # for this runtime, and the wrapper's child bridge doesn't exist
            # until the PTY launches).
            _wrapper_backed = _managed_via_wrapper_for_runtime(settings_for_pty, row["runtime"] or "")
            if _managed_terminal_backing_enabled(settings_for_pty) and (_eager_flag or _claude_needs_wrapper or _wrapper_backed):
                try:
                    await _ensure_managed_pty_for_dispatch(
                        db,
                        row["agent_id"],
                        runtime=row["runtime"],
                        settings=settings_for_pty,
                        requested_by="spawn-request",
                    )
                except Exception:
                    # The dispatch path's lazy spawn is our fallback.
                    pass

        await db.execute(
            """
            UPDATE spawn_requests
            SET status = ?, process_id = ?, session_handle = ?, session_id = ?, error = ?,
                updated_at = ?, started_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                status_value,
                req.processId or row["process_id"] or "",
                req.sessionHandle or row["session_handle"] or "",
                session_id,
                req.error or "",
                now,
                started_at,
                finished_at,
                spawn_request_id,
            ),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))).fetchone()
        updated_spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_updated", {"spawnRequestId": spawn_request_id, "status": status_value})
            if status_value == "running":
                await ws.broadcast("agent_registered", {"agentId": row["agent_id"], "runtime": row["runtime"], "sessionMode": "managed"})
                if row["status"] != "running" and str(row["initial_message"] or "").strip():
                    await ws.broadcast("dispatch_queued", {"targetAgentId": row["agent_id"]})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(updated_spec) if updated_spec else None)}
    finally:
        await db.close()


@router.get("/sessions")
async def list_sessions(request: Request, agentId: Optional[str] = None, environmentId: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
    db = await get_db()
    try:
        await _repair_superseded_recovering_sessions(db)
        await _repair_current_session_freshness(db)
        await _repair_terminal_session_consistency(db)
        where = []
        params: list[Any] = []
        if agentId:
            where.append("agent_id = ?")
            params.append(agentId)
        if environmentId:
            where.append("environment_id = ?")
            params.append(environmentId)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"SELECT * FROM agent_sessions {where_sql} ORDER BY last_seen DESC LIMIT ?",
            (*params, limit),
        )
        return {"ok": True, "sessions": [_agent_session_to_dict(row) for row in await cursor.fetchall()]}
    finally:
        await db.close()


def _default_console_command(session, workspace: str, *, interactive: bool = False) -> str:
    """Build the dashboard Console launch command for an agent session.

    Plan 3 (2026-05-25): per-runtime tail collapses to
    `adapter.console_command(...)`. The adapter owns the per-runtime quirks
    (claude interactive stays fresh, codex always resumes, pi interactive
    avoids the 026H trap, opencode is plain CLI).
    """
    from service.runtimes import adapter_for

    agent_id = str(session["agent_id"] or "").strip()
    handle = str(session["session_handle"] or "").strip()
    runtime = _normalize_runtime(session["runtime"] or "")

    try:
        adapter = adapter_for(runtime)
    except ValueError:
        return f"{runtime or 'agent'} --aify-agent {agent_id}"

    return adapter.console_command(
        agent_id=agent_id,
        handle=handle,
        interactive=interactive,
    )


@router.post("/sessions/{session_id}/console/start")
async def start_session_console(session_id: str, req: ConsoleStartRequest, request: Request):
    db = await get_db()
    try:
        session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (session["environment_id"],))).fetchone()
        if not env_row:
            raise HTTPException(409, f'Environment "{session["environment_id"]}" is not available')
        settings = await _load_settings(db)

        # Slice 3: reuse the existing live wrapper PTY for this agent
        # session when one is already attached. Avoids the symptom
        # where each "Start Console" click (or auto-attach via the
        # dashboard) spawns a fresh wrapper PTY even though a previous
        # one is still running — operator-visible "console pops up
        # again". The dispatch path (via _ensure_managed_pty_for_dispatch
        # -> _active_terminal_for_agent) already reuses; this brings the
        # manual-start path to parity.
        existing_terminal_id = str(session["terminal_id"] or "").strip()
        if existing_terminal_id:
            existing_terminal = await (await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ?",
                (existing_terminal_id,),
            )).fetchone()
            if existing_terminal:
                existing_status = str(existing_terminal["status"] or "").strip().lower()
                if existing_status in {"starting", "attached", "running", "active", "idle", "recovering"}:
                    await _append_terminal_event(
                        db,
                        existing_terminal_id,
                        "console_attach_reused_existing",
                        json.dumps({
                            "requestedBy": str(req.requestedBy or "dashboard").strip() or "dashboard",
                            "sessionId": session_id,
                            "agentId": session["agent_id"],
                        }),
                    )
                    await db.commit()
                    return {
                        "ok": True,
                        "terminal": _terminal_session_to_dict(existing_terminal),
                        "reused": True,
                    }

        # Agent-scoped virtual terminal reattach (Phase 2 follow-up).
        # The virtual terminal_session created by /agents/{id}/virtual-terminal/ensure
        # is canonical per-agent: ONE row per agent regardless of how many
        # agent_sessions exist over the agent's lifetime. The bridge creates
        # it tied to whichever agent_session was active at first dispatch,
        # but a later dashboard Console click on a DIFFERENT agent_session
        # for the same agent must attach to that same virtual terminal —
        # otherwise the dashboard would spawn a fresh pi-aify PTY console
        # and the operator sees a different terminal than the one actually
        # driving their dispatches. Skip the PTY env-supports check too:
        # virtual terminals don't need node-pty.
        agent_row_for_virtual = await (await db.execute(
            "SELECT id, runtime, runtime_state FROM agents WHERE id = ?",
            (session["agent_id"],),
        )).fetchone()
        if agent_row_for_virtual:
            agent_runtime_state = _json_loads_or(agent_row_for_virtual["runtime_state"], {}) or {}
            virtual_terminal_id = str(agent_runtime_state.get("virtualTerminalId") or "").strip()
            if virtual_terminal_id:
                virtual_terminal = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    (virtual_terminal_id,),
                )).fetchone()
                if virtual_terminal:
                    virtual_status = str(virtual_terminal["status"] or "").strip().lower()
                    virtual_command = str(virtual_terminal["command"] or "")
                    if (
                        virtual_command in VIRTUAL_RPC_COMMAND_SET
                        and virtual_status in {"starting", "running", "recovering", "active", "idle"}
                    ):
                        attach_now = _now()
                        # Point the requesting session at the canonical
                        # virtual terminal so the dashboard's session view
                        # follows it.
                        await db.execute(
                            """
                            UPDATE agent_sessions
                            SET terminal_id = ?,
                                terminal_status = ?,
                                terminal_command = ?,
                                last_seen = ?
                            WHERE id = ?
                            """,
                            (virtual_terminal_id, virtual_status, virtual_command, attach_now, session_id),
                        )
                        await _append_terminal_event(
                            db,
                            virtual_terminal_id,
                            "virtual_pi_rpc_console_attached",
                            json.dumps({
                                "requestedBy": str(req.requestedBy or "dashboard").strip() or "dashboard",
                                "sessionId": session_id,
                                "agentId": session["agent_id"],
                            }),
                        )
                        await db.commit()
                        updated_session_for_virtual = await (await db.execute(
                            "SELECT * FROM agent_sessions WHERE id = ?",
                            (session_id,),
                        )).fetchone()
                        ws_for_virtual = await _get_ws(request)
                        if ws_for_virtual:
                            await ws_for_virtual.broadcast(
                                "terminal_started",
                                {
                                    "terminalId": virtual_terminal_id,
                                    "sessionId": session_id,
                                    "agentId": session["agent_id"],
                                    "virtual": True,
                                    "reused": True,
                                },
                            )
                        return {
                            "ok": True,
                            "terminal": _terminal_session_to_dict(virtual_terminal),
                            "session": _agent_session_to_dict(updated_session_for_virtual),
                            "reused": True,
                            "virtual": True,
                        }

        runtime = _normalize_runtime(session["runtime"] or "")
        if runtime == "pi":
            environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
            if str(environment.get("status") or "").lower() != "online":
                raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
            if not str(session["session_handle"] or "").strip() and not bool(req.freshContext):
                raise HTTPException(409, 'Pi Console needs a session handle to preserve context. Set a handle or request freshContext=true.')
            workspace, _workspace_root = _workspace_for_environment(environment, req.workspace, session["workspace"] or "")
            terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            now = _now()
            bridge_id = str(environment.get("bridgeId") or "").strip()
            virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME["pi"]
            requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
            await db.execute(
                """
                INSERT INTO terminal_sessions (
                    id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                    output, status, requested_by, created_at, updated_at, stopped_at, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    terminal_id,
                    session_id,
                    session["agent_id"],
                    session["environment_id"],
                    bridge_id,
                    session["runtime"],
                    workspace,
                    virtual_command,
                    "",
                    "running",
                    requested_by,
                    now,
                    now,
                    None,
                    "",
                ),
            )
            await _append_terminal_event(
                db,
                terminal_id,
                "virtual_pi_rpc_console_started",
                json.dumps({"requestedBy": requested_by, "sessionId": session_id, "workspace": workspace}),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'managed',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = 'running',
                    terminal_command = ?,
                    terminal_workspace = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (bridge_id, terminal_id, virtual_command, workspace, now, session_id),
            )
            next_runtime_state = _json_loads_or((agent_row_for_virtual["runtime_state"] if agent_row_for_virtual else "") or "{}", {}) or {}
            next_runtime_state["virtualTerminal"] = True
            next_runtime_state["virtualTerminalId"] = terminal_id
            await db.execute(
                "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_runtime_state), now, session["agent_id"]),
            )
            # The agent now has a live worker (virtualTerminalId + terminal_status
            # running). Invalidate the live-status cache so it recomputes to online
            # immediately instead of lying `available` until the 60s sweep.
            await _invalidate_agent_live_state(db, session["agent_id"])
            await db.commit()
            terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
            updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
            ws_for_virtual = await _get_ws(request)
            if ws_for_virtual:
                await ws_for_virtual.broadcast(
                    "terminal_started",
                    {"terminalId": terminal_id, "sessionId": session_id, "agentId": session["agent_id"], "virtual": True},
                )
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(terminal),
                "session": _agent_session_to_dict(updated_session),
                "reused": False,
                "virtual": True,
            }

        environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
        if not _environment_supports_terminal(environment, session["runtime"]):
            env_id = environment.get("id")
            if not bool(environment.get("terminal")) or not bool(environment.get("pty")):
                # Whole-environment PTY capability is off — not a per-runtime
                # issue. The bridge on that host reports no terminal/pty
                # (usually node-pty is not installed/built there).
                detail = (
                    f'Environment "{env_id}" has no PTY/terminal capability — its bridge reports '
                    f'terminal={bool(environment.get("terminal"))}, pty={bool(environment.get("pty"))}. '
                    f'This blocks the Console for ALL runtimes there (not just "{session["runtime"]}"). '
                    f'Fix: install/build node-pty for the aify-comms bridge on that host '
                    f'(reinstall via install.sh and restart the bridge), then retry. '
                    f'Use an environment that advertises terminal support in the meantime.'
                )
            else:
                advertised = ", ".join(
                    str(r) for r in (environment.get("terminalRuntimes") or [])
                ) or "none"
                detail = (
                    f'Environment "{env_id}" supports the Console but not for runtime '
                    f'"{session["runtime"]}". It advertises terminal runtimes: {advertised}. '
                    f'Spawn/select a supported runtime, or update that bridge.'
                )
            raise HTTPException(409, detail)
        if runtime == "pi" and not str(session["session_handle"] or "").strip() and not bool(req.freshContext):
            raise HTTPException(409, 'Pi Console needs a session handle to preserve context. Set a handle or request freshContext=true.')

        workspace, _workspace_root = _workspace_for_environment(environment, req.workspace, session["workspace"] or "")
        terminal_id = f"term_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        command = str(req.command or "").strip() or _default_console_command(session, workspace, interactive=True)
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        bridge_id = str(environment.get("bridgeId") or "").strip()
        await db.execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                output, status, requested_by, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                session_id,
                session["agent_id"],
                session["environment_id"],
                bridge_id,
                session["runtime"],
                workspace,
                command,
                "",
                "starting",
                requested_by,
                now,
                now,
                None,
                "",
            ),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "console_start_requested",
            json.dumps({"requestedBy": requested_by, "sessionId": session_id, "workspace": workspace, "command": command}),
        )
        await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=session["environment_id"],
            bridge_id=bridge_id,
            action="start",
            requested_by=requested_by,
            body=command,
        )
        # Mirror _ensure_managed_pty_for_dispatch: when the operator has
        # opted into auto-confirming the Claude dev-channel "WARNING:
        # Loading development channels" prompt, enqueue the startup Enter
        # for the manually-started console too. Without this, manual
        # console starts hit the prompt and require the operator to
        # actually press Enter — which the per-keystroke path makes
        # awkward (the prompt is a TUI menu, not a plain line).
        if runtime == "claude-code" and bool(settings.get("console_auto_confirm_claude_dev_channels")):
            await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=session["environment_id"],
                bridge_id=bridge_id,
                action="input",
                requested_by=requested_by,
                body="\r",
            )
            await _append_terminal_event(
                db,
                terminal_id,
                "console_channel_prompt_auto_confirm_requested",
                json.dumps({"requestedBy": requested_by, "reason": "confirm Claude development channel prompt on manual console start"}),
            )
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'console',
                owner_bridge_id = ?,
                terminal_id = ?,
                terminal_status = 'starting',
                terminal_command = ?,
                terminal_workspace = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (bridge_id, terminal_id, command, workspace, now, session_id),
        )
        await db.commit()
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_started", {"terminalId": terminal_id, "sessionId": session_id, "agentId": session["agent_id"]})
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "session": _agent_session_to_dict(updated_session),
        }
    finally:
        await db.close()


VIRTUAL_PI_RPC_COMMAND = "aify://virtual-rpc/pi"
VIRTUAL_HERMES_RPC_COMMAND = "aify://virtual-rpc/hermes"
VIRTUAL_CODEX_RPC_COMMAND = "aify://virtual-rpc/codex"
VIRTUAL_OPENCODE_RPC_COMMAND = "aify://virtual-rpc/opencode"
VIRTUAL_RPC_COMMANDS_BY_RUNTIME = {
    "pi": VIRTUAL_PI_RPC_COMMAND,
    "hermes": VIRTUAL_HERMES_RPC_COMMAND,
    "codex": VIRTUAL_CODEX_RPC_COMMAND,
    "opencode": VIRTUAL_OPENCODE_RPC_COMMAND,
}
VIRTUAL_RPC_COMMAND_SET = set(VIRTUAL_RPC_COMMANDS_BY_RUNTIME.values())


@router.get("/agents/{agent_id}/pi-session-state")
async def get_agent_pi_session_state(agent_id: str):
    """Watchdog readout for omp-aify (Phase 4).

    Reports whether the aify-comms bridge currently drives this agent's pi
    session through a persistent RPC child. The omp-aify wrapper queries this
    before exec'ing omp; if the bridge owns the session it refuses to start,
    avoiding two processes racing on the same OMP session-id (the upstream
    RPC channel has no multiplexing — see DECISIONS.md). Soft mutex: this
    endpoint never kills anything. It is a fast read against
    terminal_sessions + agents.runtime_state.
    """
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        runtime_state = _json_loads_or(agent_row["runtime_state"], {}) or {}
        virtual_terminal_id = str(runtime_state.get("virtualTerminalId") or "").strip()
        bridge_owned = False
        terminal_payload: Optional[dict[str, Any]] = None
        if virtual_terminal_id:
            row = await (await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ?",
                (virtual_terminal_id,),
            )).fetchone()
            if row and (row["command"] or "") == VIRTUAL_PI_RPC_COMMAND:
                status = str(row["status"] or "").strip().lower()
                if status in {"starting", "running", "recovering", "active", "idle"}:
                    bridge_owned = True
                    terminal_payload = _terminal_session_to_dict(row)
        return {
            "ok": True,
            "agentId": agent_id,
            "runtime": _normalize_runtime(agent_row["runtime"] or ""),
            "bridgeOwned": bridge_owned,
            "virtualTerminalId": virtual_terminal_id if bridge_owned else "",
            "terminal": terminal_payload,
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/virtual-terminal/ensure")
async def ensure_virtual_terminal(agent_id: str, req: VirtualTerminalEnsureRequest, request: Request):
    """Bridge-driven creation of a synthesized terminal_session row.

    Managed pi runs use a persistent `omp --mode rpc` child whose AgentSessionEvent
    stream is synthesized by the bridge into a human-readable terminal_output
    feed. There is no real PTY — the bridge owns the lifecycle. This endpoint is
    idempotent: a second call for the same agent on the same bridge returns the
    existing virtual terminal row. See docs/plans/pi-persistent-rpc.md.
    """
    db = await get_db()
    try:
        agent = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        bridge_id = str(req.bridgeId or "").strip()
        if not bridge_id:
            raise HTTPException(400, "bridgeId is required")
        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "pi")
        virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME.get(runtime)
        if not virtual_command:
            raise HTTPException(
                409,
                f'Virtual terminal is available for runtimes {sorted(VIRTUAL_RPC_COMMANDS_BY_RUNTIME)} only (got runtime="{runtime}")',
            )

        env_row = await (await db.execute(
            "SELECT * FROM environments WHERE bridge_id = ? ORDER BY last_seen DESC LIMIT 1",
            (bridge_id,),
        )).fetchone()
        if not env_row:
            raise HTTPException(404, f'No environment registered for bridgeId "{bridge_id}"')
        environment_id = env_row["id"]

        session_row = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
              AND environment_id = ?
              AND status IN ('running', 'recovering', 'starting', 'managed-warm')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, environment_id),
        )).fetchone()
        if not session_row:
            raise HTTPException(
                409,
                f'No active agent_session for "{agent_id}" on environment "{environment_id}". '
                f'The bridge should dispatch at least once before requesting a virtual terminal.',
            )
        session_id = session_row["id"]

        # Agent-scoped lookup: one virtual terminal per agent across all of
        # its agent_sessions. If a prior session created the row and is now
        # stale, re-anchor the terminal's session_id (and the new session's
        # terminal_id pointer) to the requesting session so the
        # CASCADE-on-delete FK keeps the row alive once the original
        # session row is eventually cleaned up.
        existing = await (await db.execute(
            """
            SELECT *
            FROM terminal_sessions
            WHERE agent_id = ?
              AND command = ?
              AND status NOT IN ('stopped', 'failed')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (agent_id, virtual_command),
        )).fetchone()
        if existing:
            existing_session_id = existing["session_id"]
            if existing_session_id != session_id:
                rebind_now = _now()
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET session_id = ?,
                        bridge_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (session_id, bridge_id, rebind_now, existing["id"]),
                )
                # Detach the prior session from the terminal but keep its
                # historical record otherwise intact.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = '',
                        terminal_command = ''
                    WHERE id = ? AND terminal_id = ?
                    """,
                    (existing_session_id, existing["id"]),
                )
                # Point the new active session at the terminal.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = ?,
                        terminal_status = 'running',
                        terminal_command = ?,
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (existing["id"], virtual_command, rebind_now, session_id),
                )
                await _append_terminal_event(
                    db,
                    existing["id"],
                    "virtual_pi_rpc_reanchored",
                    json.dumps({
                        "fromSessionId": existing_session_id,
                        "toSessionId": session_id,
                        "bridgeId": bridge_id,
                    }),
                )
                await db.commit()
                existing = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    (existing["id"],),
                )).fetchone()
                session_row = await (await db.execute(
                    "SELECT * FROM agent_sessions WHERE id = ?",
                    (session_id,),
                )).fetchone()
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(existing),
                "session": _agent_session_to_dict(session_row),
                "reused": True,
            }

        # Plan 4 (2026-05-25) synth-terminal deprecation: when this runtime
        # routes through a *-aify wrapper PTY, the wrapper IS the terminal —
        # don't create a synth row in parallel. Reuse of a pre-existing synth
        # row (handled above) is still allowed for backwards compatibility
        # and for the hard-failure fallback path that may seed one explicitly.
        settings_for_synth_gate = await _load_settings(db)
        if not _synth_terminal_should_be_created(runtime, settings_for_synth_gate):
            raise HTTPException(
                409,
                f'Synth terminal creation skipped for wrapper-backed runtime "{runtime}" '
                f'(Plan 4 deprecation — the wrapper PTY is the terminal).',
            )

        workspace = str(req.workspace or session_row["workspace"] or "").strip()
        terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        requested_by = str(req.requestedBy or "bridge-rpc").strip() or "bridge-rpc"
        await db.execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                output, status, requested_by, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                session_id,
                agent_id,
                environment_id,
                bridge_id,
                runtime,
                workspace,
                virtual_command,
                "",
                "running",
                requested_by,
                now,
                now,
                None,
                "",
            ),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            f"virtual_{runtime}_rpc_attached",
            json.dumps({
                "requestedBy": requested_by,
                "sessionId": session_id,
                "bridgeId": bridge_id,
                "sessionHandle": req.sessionHandle or "",
            }),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = ?,
                terminal_status = 'running',
                terminal_command = ?,
                terminal_workspace = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (terminal_id, virtual_command, workspace, now, session_id),
        )
        next_runtime_state = _json_loads_or(agent["runtime_state"], {}) or {}
        next_runtime_state["virtualTerminal"] = True
        next_runtime_state["virtualTerminalId"] = terminal_id
        await db.execute(
            """
            UPDATE agents
            SET runtime_state = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(next_runtime_state), now, agent_id),
        )
        # The agent now has a live worker (virtualTerminalId + terminal_status
        # running). Invalidate the live-status cache so it recomputes to online
        # immediately instead of lying `available` until the 60s sweep.
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "terminal_started",
                {
                    "terminalId": terminal_id,
                    "sessionId": session_id,
                    "agentId": agent_id,
                    "virtual": True,
                },
            )
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "session": _agent_session_to_dict(updated_session),
            "reused": False,
        }
    finally:
        await db.close()


@router.get("/terminals/{terminal_id}")
async def get_terminal(terminal_id: str):
    await TERMINAL_OUTPUT_WRITES.flush_terminal(terminal_id)
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        events = await (await db.execute(
            "SELECT * FROM terminal_events WHERE terminal_id = ? ORDER BY id ASC LIMIT 200",
            (terminal_id,),
        )).fetchall()
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "events": [_terminal_event_to_dict(row) for row in events],
        }
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/output")
async def append_terminal_output(terminal_id: str, req: TerminalOutputRequest, request: Request):
    db = await get_db()
    try:
        # Deliberately omit the (up to 64KB) `output` blob: this is the
        # high-frequency ingest path and never needs the existing buffer. The
        # queue flush re-reads only what it concatenates.
        terminal = await (await db.execute(
            """
            SELECT id, session_id, agent_id, environment_id, bridge_id, runtime,
                   workspace, command, output_seq, status, requested_by,
                   created_at, updated_at, stopped_at, error
            FROM terminal_sessions WHERE id = ?
            """,
            (terminal_id,),
        )).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        # Bridge-ownership check: for REAL PTY terminals (a node-pty process
        # spawned by one bridge), a mismatched bridge_id MUST 409 — only the
        # owning bridge can write to its PTY. But synthesized virtual rpc
        # terminals (pi/hermes/codex/opencode) are just frame buffers with no
        # underlying owned process; sequential bridges that take over an
        # agent (e.g., aify-comms restarted between dispatches) need to
        # write to the SAME terminal_session row so the operator's Console
        # view stays continuous. Operator-reported 2026-05-22:
        # graph-tester-pi's synth terminal stopped updating at the
        # timestamp of the bridge that originally created it — every later
        # dispatch was rejected with 409.
        new_bridge_id = str(req.bridgeId or "").strip()
        existing_bridge_id = str(terminal["bridge_id"] or "").strip()
        terminal_command = str(terminal["command"] or "")
        is_virtual_rpc = terminal_command in VIRTUAL_RPC_COMMAND_SET
        if new_bridge_id and existing_bridge_id and new_bridge_id != existing_bridge_id:
            if is_virtual_rpc:
                # Transfer ownership of the synth terminal to the new bridge.
                # Audit so operators see the takeover in the event log.
                #
                # Revive if previously stopped — the bridge-supersession
                # cleanup (`_stop_virtual_terminals_for_superseded_bridges`)
                # can race against an in-flight dispatch on the new bridge:
                # supersession stops the row, then the new bridge's
                # /output POST arrives. Operator-reported 2026-05-22:
                # codex synth terminal showed "started then stopped" yet
                # the agent still replied — frames were accumulating
                # in terminal_events while the row was stale-stopped,
                # leaving the dashboard rendering "terminal is not
                # running" despite a healthy stream of frames. The
                # arriving POST is hard proof the new bridge is
                # actively writing, so undo the stale stop.
                current_status = str(terminal["status"] or "").strip().lower()
                if current_status == "stopped":
                    await db.execute(
                        """
                        UPDATE terminal_sessions
                        SET bridge_id = ?, status = 'running', stopped_at = NULL, error = ''
                        WHERE id = ?
                        """,
                        (new_bridge_id, terminal_id),
                    )
                else:
                    await db.execute(
                        "UPDATE terminal_sessions SET bridge_id = ? WHERE id = ?",
                        (new_bridge_id, terminal_id),
                    )
                await _append_terminal_event(
                    db,
                    terminal_id,
                    "virtual_rpc_bridge_takeover",
                    json.dumps({
                        "from": existing_bridge_id,
                        "to": new_bridge_id,
                        "revived": current_status == "stopped",
                    }),
                )
                # Commit immediately — the endpoint's only other commit
                # is inside the _TERMINAL_END_STATUSES branch, which
                # doesn't fire for normal "running" output POSTs. Without
                # this, the bridge_id transfer + revive would silently
                # be lost on the next connection (failing the takeover
                # contract for any subsequent reader).
                await db.commit()
            else:
                raise HTTPException(409, "Terminal is owned by a different bridge")
        status = str(req.status or "").strip()
        next_seq = await TERMINAL_OUTPUT_WRITES.enqueue(
            terminal_id,
            req.output or "",
            status=status,
            base_seq=int(terminal["output_seq"] or 0),
            autoschedule=not bool(getattr(request.app.state, "testing", False)),
        )
        if status in _TERMINAL_END_STATUSES:
            now = _now()
            summary = f"Terminal {status} before an explicit reply was recorded."
            await _close_active_terminal_runs_for_terminal(db, terminal, status, now=now, reason=summary)
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?,
                    updated_at = ?,
                    stopped_at = COALESCE(stopped_at, ?)
                WHERE id = ?
                """,
                (status, now, now, terminal_id),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = 'managed',
                    last_seen = ?
                WHERE id = ?
                """,
                (status, now, terminal["session_id"]),
            )
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
            await db.commit()
        # Do NOT broadcast per-POST here: concurrent POSTs reorder vs seq and
        # the dashboard's seq-dedupe then drops frames (scrambled console).
        # Hand the ws manager to the write queue, which emits one ordered,
        # coalesced, post-commit broadcast per flush instead.
        ws = await _get_ws(request)
        if ws is not None:
            TERMINAL_OUTPUT_WRITES.ws_manager = ws
        # Ingest ack only — the response intentionally carries no output buffer
        # (clients read full output via GET /terminals/{id}). The sole caller
        # is the bridge, which uses outputSeq/status and ignores the rest.
        terminal_payload = _terminal_session_to_dict(terminal)
        terminal_payload["outputSeq"] = next_seq
        if status:
            terminal_payload["status"] = status
        return {"ok": True, "terminal": terminal_payload}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/input")
async def send_terminal_input(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="input",
            requested_by=requested_by,
            body=req.body or "",
        )
        await _append_terminal_event(db, terminal_id, "terminal_input_requested", json.dumps({"requestedBy": requested_by, "controlId": control_id}))
        await db.commit()
        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal_id, "action": "input"})
        return {"ok": True, "control": _terminal_control_to_dict(control)}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/resize")
async def resize_terminal(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="resize",
            requested_by=requested_by,
            cols=int(req.cols or 0),
            rows=int(req.rows or 0),
        )
        await _append_terminal_event(db, terminal_id, "terminal_resize_requested", json.dumps({"requestedBy": requested_by, "cols": req.cols or 0, "rows": req.rows or 0}))
        await db.commit()
        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal_id, "action": "resize"})
        return {"ok": True, "control": _terminal_control_to_dict(control)}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/stop")
async def stop_terminal(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        now = _now()
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (terminal["environment_id"],))).fetchone()
        settings = await _load_settings(db)
        env_status = _environment_effective_status(
            env_row,
            offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
        ) if env_row else "offline"
        current_bridge_id = str((env_row["bridge_id"] if env_row else "") or "").strip()
        terminal_bridge_id = str(terminal["bridge_id"] or "").strip()
        terminal_status = str(terminal["status"] or "").strip().lower()
        bridge_can_claim = bool(
            terminal_bridge_id
            and current_bridge_id
            and terminal_bridge_id == current_bridge_id
            and env_status in {"online", "degraded"}
        )
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="stop",
            requested_by=requested_by,
            body=req.body or "",
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "console_stop_requested",
            json.dumps({"requestedBy": requested_by, "body": req.body or "", "controlId": control_id}),
        )
        if terminal_status in {"stopped", "failed"} or not bridge_can_claim:
            reason = "Terminal bridge is no longer current; stop reconciled in control plane."
            await db.execute(
                """
                UPDATE terminal_controls
                SET status = 'completed',
                    claimed_at = COALESCE(claimed_at, ?),
                    handled_at = ?
                WHERE id = ?
                """,
                (now, now, control_id),
            )
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = 'stopped',
                    updated_at = ?,
                    stopped_at = COALESCE(stopped_at, ?),
                    error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
                WHERE id = ?
                """,
                (now, now, reason if terminal_status not in {"stopped", "failed"} else "", terminal_id),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'managed',
                    terminal_status = 'stopped',
                    last_seen = ?
                WHERE id = ?
                """,
                (now, terminal["session_id"]),
            )
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
            # _clear_console_terminal_binding only invalidates when the agent's
            # consoleTerminal pointer matches (no-ops for virtual/RPC terminals,
            # whose pointer is virtualTerminalId). Invalidate explicitly here —
            # mirroring the sibling bridge-reported completion path — so the
            # reconciled stop drops the agent out of `online`/`working`
            # immediately rather than lying until the 60s sweep.
            await _invalidate_agent_live_state(db, terminal["agent_id"])
            await _append_terminal_event(
                db,
                terminal_id,
                "console_stop_reconciled",
                json.dumps({
                    "requestedBy": requested_by,
                    "reason": reason,
                    "terminalBridge": terminal_bridge_id,
                    "environmentBridge": current_bridge_id,
                    "environmentStatus": env_status,
                }),
            )
            await db.commit()
            updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
            return {"ok": True, "terminal": _terminal_session_to_dict(updated)}
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopping', updated_at = ?
            WHERE id = ?
            """,
            (now, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_status = 'stopping',
                last_seen = ?
            WHERE id = ?
            """,
            (now, terminal["session_id"]),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
        return {"ok": True, "terminal": _terminal_session_to_dict(updated)}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/report-dead")
async def report_terminal_dead(terminal_id: str, req: TerminalDeadReport, request: Request):
    """Host-reported dead-PTY signal (WS4 Task 4.2).

    The server cannot probe a remote host's PID; only the OWNING environment
    bridge can. When a bridge observes that one of its `attached` console PTY
    rows has a `process_id` that is no longer alive locally, it POSTs here so the
    server can mark the row stopped, close any active runs, clear the console
    binding, and invalidate the agent's live state (a frozen/crashed console
    can otherwise keep manufacturing presence).

    SAFETY: if a `processId` is supplied it MUST match the stored process_id.
    A bridge that has since restarted the console owns a NEW pid; a stale
    report carrying the OLD pid must NOT stop the live row. Already-terminal
    rows are a harmless idempotent no-op.
    """
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        now = _now()
        current_status = str(terminal["status"] or "").strip().lower()
        # Idempotent: already terminal → nothing to do.
        if current_status in _TERMINAL_END_STATUSES:
            return {"ok": True, "terminal": _terminal_session_to_dict(terminal), "changed": False}
        # PID guard: a supplied pid must match the stored process_id so a stale
        # report can't stop a row a restarted bridge now owns with a NEW pid.
        reported_pid = str(req.processId or "").strip()
        stored_pid = str(terminal["process_id"] or "").strip()
        if reported_pid and stored_pid and reported_pid != stored_pid:
            await _append_terminal_event(
                db,
                terminal_id,
                "console_dead_report_ignored",
                json.dumps({"reportedPid": reported_pid, "storedPid": stored_pid, "bridgeId": req.bridgeId or ""}),
            )
            await db.commit()
            return {"ok": True, "terminal": _terminal_session_to_dict(terminal), "changed": False, "ignored": "pid-mismatch"}
        reason = str(req.reason or "").strip() or "Console PTY process is no longer alive (host-reported)."
        # Close any active runs bound to this terminal before stopping the row.
        await _close_active_terminal_runs_for_terminal(
            db,
            terminal,
            "stopped",
            now=now,
            reason=reason,
        )
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                terminal_status = 'stopped',
                last_seen = ?
            WHERE id = ?
            """,
            (now, terminal["session_id"]),
        )
        await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
        await _invalidate_agent_live_state(db, terminal["agent_id"])
        await _append_terminal_event(
            db,
            terminal_id,
            "console_dead_reported",
            json.dumps({"reportedPid": reported_pid, "bridgeId": req.bridgeId or "", "reason": reason}),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
        return {"ok": True, "terminal": _terminal_session_to_dict(updated), "changed": True}
    finally:
        await db.close()


@router.post("/terminals/controls/claim")
async def claim_terminal_controls(req: TerminalControlClaim):
    db = await get_db()
    try:
        now = _now()
        cursor = await db.execute(
            """
            SELECT *
            FROM terminal_controls
            WHERE environment_id = ?
              AND COALESCE(bridge_id, '') = ?
              AND status = 'pending'
            ORDER BY requested_at ASC, id ASC
            LIMIT 20
            """,
            (req.environmentId, req.bridgeId),
        )
        controls = await cursor.fetchall()
        if controls:
            ids = [row["id"] for row in controls]
            await db.executemany(
                "UPDATE terminal_controls SET status = 'claimed', claimed_at = ? WHERE id = ? AND status = 'pending'",
                [(now, control_id) for control_id in ids],
            )
            await db.commit()
            refreshed = []
            for control_id in ids:
                row = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
                if row:
                    refreshed.append(row)
            controls = refreshed
        # Attach the target terminal's stored PTY root pid so a claiming bridge
        # can kill-by-pid when its in-memory terminals Map misses (orphaned PTY,
        # owning bridge gone). The claim is already env+bridge scoped, so the pid
        # only ever reaches the bridge for terminals on its own machine.
        out = []
        for row in controls:
            term_row = await (await db.execute(
                "SELECT process_id, agent_id, runtime FROM terminal_sessions WHERE id = ?",
                (row["terminal_id"],),
            )).fetchone()
            pid = str((term_row["process_id"] if term_row else "") or "")
            agent_id = str((term_row["agent_id"] if term_row else "") or "")
            runtime = str((term_row["runtime"] if term_row else "") or "")
            # Surface the target agent's session_mode so a claiming bridge can
            # decide whether a `stop` control needs a MANAGED-HERMES triad teardown
            # (fix/hermes-leak P2). Best-effort; resident/unknown → "".
            session_mode = ""
            if agent_id:
                agent_row = await (await db.execute(
                    "SELECT session_mode FROM agents WHERE id = ?",
                    (agent_id,),
                )).fetchone()
                session_mode = _normalize_session_mode((agent_row["session_mode"] if agent_row else "") or "")
            out.append(_terminal_control_to_dict(
                row, pid=pid, agent_id=agent_id, runtime=runtime, session_mode=session_mode,
            ))
        return {"ok": True, "controls": out}
    finally:
        await db.close()


@router.patch("/terminals/controls/{control_id}")
async def update_terminal_control(control_id: str, req: TerminalControlUpdate, request: Request):
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, f'Unsupported terminal control status "{req.status}"')
    db = await get_db()
    try:
        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        if not control:
            raise HTTPException(404, f'Terminal control "{control_id}" not found')
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (control["terminal_id"],))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{control["terminal_id"]}" not found')
        now = _now()
        await db.execute(
            """
            UPDATE terminal_controls
            SET status = ?, handled_at = ?, error = ?
            WHERE id = ?
            """,
            (status, now, req.error or "", control_id),
        )
        # Persist the PTY root pid reported by the owning bridge (start-control
        # attach). Stored so Dashboard Stop/Restart can kill-by-pid even if the
        # owning bridge later dies and the PTY is orphaned. Only set on a real
        # positive value — never blank out an existing pid.
        report_pid = str(req.processId or "").strip()
        if report_pid:
            await db.execute(
                "UPDATE terminal_sessions SET process_id = ? WHERE id = ?",
                (report_pid, terminal["id"]),
            )
        terminal_status = str(req.terminalStatus or "").strip()
        if status == "failed":
            terminal_status = terminal_status or "failed"
        if control["action"] == "stop" and status == "completed":
            terminal_status = terminal_status or "stopped"
        if terminal_status:
            terminal_status_norm = terminal_status.strip().lower()
            if terminal_status_norm in _TERMINAL_END_STATUSES:
                await _close_active_terminal_runs_for_terminal(
                    db,
                    terminal,
                    terminal_status_norm,
                    now=now,
                    reason=f"Terminal {terminal_status_norm} before an explicit reply was recorded.",
                )
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?, updated_at = ?, stopped_at = CASE WHEN ? IN ('stopped','failed') THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                    error = CASE WHEN ? = 'failed' THEN ? ELSE error END
                WHERE id = ?
                """,
                (terminal_status, now, terminal_status, now, status, req.error or "", terminal["id"]),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = CASE WHEN ? IN ('stopped','failed') THEN 'managed' ELSE owner_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (terminal_status, terminal_status, now, terminal["session_id"]),
            )
        if terminal_status in {"stopped", "failed"}:
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal["id"], now=now)
        if terminal_status.strip().lower() in _TERMINAL_END_STATUSES:
            await _invalidate_agent_live_state(db, terminal["agent_id"])
        if req.output:
            latest_terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))).fetchone()
            await _append_terminal_output(db, latest_terminal or terminal, req.output, status=terminal_status)
        await _append_terminal_event(
            db,
            terminal["id"],
            f"terminal_control_{status}",
            json.dumps({"controlId": control_id, "action": control["action"], "error": req.error or ""}),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        updated_terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_updated", {"terminalId": terminal["id"], "controlId": control_id, "status": status})
        return {"ok": True, "control": _terminal_control_to_dict(updated), "terminal": _terminal_session_to_dict(updated_terminal)}
    finally:
        await db.close()


@router.post("/sessions/{session_id}/control")
async def control_session(session_id: str, req: SessionControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"stop", "restart", "recover", "resume", "recreate", "cli_takeover"}:
        raise HTTPException(400, f'Unsupported session control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        now = _now()
        agent_id = session["agent_id"]
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if active_run:
            control_id = await _append_dispatch_control(
                db,
                active_run["runId"],
                from_agent=req.from_agent or "dashboard",
                action="interrupt",
                body=req.body or f"Session {action} requested from dashboard.",
            )

        spawn_request_row = None
        spawn_spec_row = None
        cancelled_spawns = 0
        if action in {"restart", "recover", "resume", "recreate"}:
            pending_cursor = await db.execute(
                """
                SELECT *
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (agent_id,),
            )
            pending_spawn = await pending_cursor.fetchone()
            if pending_spawn:
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" already has pending spawn request "{pending_spawn["id"]}" ({pending_spawn["status"]}).',
                )

        if action in {"restart", "recover", "resume", "recreate"}:
            spec_id = str(session["spawn_spec_id"] or "").strip()
            if not spec_id:
                raise HTTPException(409, f'Session "{session_id}" has no stored spawn spec to resume')
            spec_cursor = await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))
            spawn_spec_row = await spec_cursor.fetchone()
            if not spawn_spec_row:
                raise HTTPException(409, f'Session "{session_id}" references missing spawn spec "{spec_id}"')
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (spawn_spec_row["environment_id"],))
            env_row = await env_cursor.fetchone()
            if not env_row:
                raise HTTPException(409, f'Environment "{spawn_spec_row["environment_id"]}" is not available')

            agent_cursor = await db.execute("SELECT role, name FROM agents WHERE id = ?", (agent_id,))
            agent_row = await agent_cursor.fetchone()
            environment = _environment_record_to_dict(env_row)
            if str(environment.get("status") or "").lower() != "online":
                raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}; assign a live environment before {action}.')
            workspace = _normalize_workspace_for_environment(environment, spawn_spec_row["workspace"] or session["workspace"] or "")
            workspace_root = _workspace_root_for(environment, workspace)
            request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            resume_policy = "fresh_context" if action == "recreate" else "native_first"
            request_session_handle = "" if action == "recreate" else (session["session_handle"] or "")
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
                    req.from_agent or "dashboard",
                    spawn_spec_row["environment_id"],
                    agent_id,
                    (agent_row["role"] if agent_row else "") or "coder",
                    (agent_row["name"] if agent_row else "") or agent_id,
                    spawn_spec_row["runtime"],
                    workspace,
                    workspace_root,
                    req.body or "",
                    req.priority or "normal",
                    req.subject or f"{action.title()} {agent_id}",
                    spawn_spec_row["mode"] or session["mode"] or "managed-warm",
                    resume_policy,
                    "queued",
                    request_session_handle,
                    now,
                    now,
                ),
            )
            spawn_request_row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
            if action == "recreate":
                await db.execute(
                    """
                    UPDATE agents
                    SET session_handle = '',
                        runtime_state = '{}',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (now, agent_id),
                )

        next_status = {
            "stop": "stopped",
            "restart": "restarting",
            "recover": "recovering",
            "resume": "recovering",
            "recreate": "ended",
            "cli_takeover": "cli-takeover",
        }[action]
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = ?, last_seen = ?, ended_at = CASE WHEN ? IN ('stopped','restarting','recovering','ended') THEN ? ELSE ended_at END
            WHERE id = ?
            """,
            (next_status, now, next_status, now, session_id),
        )
        if action in {"stop", "cli_takeover"}:
            pending_spawn_cursor = await db.execute(
                """
                SELECT id
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                """,
                (agent_id,),
            )
            for pending_spawn in await pending_spawn_cursor.fetchall():
                await db.execute(
                    """
                    UPDATE spawn_requests
                    SET status = 'cancelled',
                        error = ?,
                        finished_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ('queued', 'claimed', 'starting')
                    """,
                    (
                        f'Session "{session_id}" was {"paused for CLI takeover" if action == "cli_takeover" else "stopped from the dashboard"} before spawn completed.',
                        now,
                        now,
                        pending_spawn["id"],
                    ),
                )
                cancelled_spawns += 1
            if action == "cli_takeover":
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'stopped',
                        status_note = ?,
                        launch_mode = 'none',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        "Paused for direct CLI takeover. Close the CLI session and use Sessions -> Recover/Restart to return control to the dashboard.",
                        now,
                        agent_id,
                    ),
                )
            else:
                agent_current = await (await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
                if agent_current and _normalize_session_mode(agent_current["session_mode"] or "resident") == "resident":
                    await db.execute(
                        """
                        UPDATE agents
                        SET status = 'stopped',
                            status_note = ?,
                            launch_mode = 'none',
                            last_seen = ?
                        WHERE id = ?
                        """,
                        ("Resident session stop requested from dashboard; live bridge should terminate the CLI host.", now, agent_id),
                    )
                else:
                    await db.execute(
                        "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END, last_seen = ? WHERE id = ?",
                        (now, agent_id),
                    )
        else:
            await db.execute(
                "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'idle' END, last_seen = ? WHERE id = ?",
                (now, agent_id),
            )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_control_requested", {"sessionId": session_id, "agentId": agent_id, "action": action})
            if spawn_request_row:
                await ws.broadcast(
                    "spawn_request_created",
                    {"spawnRequestId": spawn_request_row["id"], "environmentId": spawn_request_row["environment_id"]},
                )
        return {
            "ok": True,
            "action": action,
            "session": _agent_session_to_dict(updated),
            "interruptControlId": control_id,
            "cancelledSpawns": cancelled_spawns,
            "spawnRequest": _spawn_request_to_dict(spawn_request_row, _spawn_spec_to_dict(spawn_spec_row) if spawn_spec_row else None) if spawn_request_row else None,
        }
    finally:
        await db.close()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        status = str(session["status"] or "").strip().lower()
        if status not in _SESSION_DELETE_ALLOWED_STATUSES:
            raise HTTPException(
                409,
                f'Session "{session_id}" is {status or "active"}; stop or finish it before deleting the session record.',
            )

        terminal_rows = await (await db.execute("SELECT * FROM terminal_sessions WHERE session_id = ?", (session_id,))).fetchall()
        stale_active_terminal_ids = [
            terminal["id"]
            for terminal in terminal_rows
            if str(terminal["status"] or "").strip().lower() not in _TERMINAL_DELETE_ALLOWED_STATUSES
        ]

        for terminal in terminal_rows:
            await db.execute("DELETE FROM terminal_controls WHERE terminal_id = ?", (terminal["id"],))
            await db.execute("DELETE FROM terminal_events WHERE terminal_id = ?", (terminal["id"],))
        await db.execute("DELETE FROM terminal_sessions WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
        await db.commit()

        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_deleted", {"sessionId": session_id, "agentId": session["agent_id"]})
        return {
            "ok": True,
            "deleted": True,
            "sessionId": session_id,
            "agentId": session["agent_id"],
            "staleActiveTerminalsDeleted": stale_active_terminal_ids,
        }
    finally:
        await db.close()


# ─── Agents ──────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents(request: Request):
    db = await get_db()
    try:
        repaired_active_runs = await _repair_unusable_active_runs(db)
        settings = await _load_settings(db)
        await _refresh_expired_agent_live_states(db, settings=settings)
        if repaired_active_runs:
            await db.commit()
        cursor = await db.execute(
            """
            SELECT a.*, ls.status AS live_status, ls.reason AS live_reason, ls.refresh_after AS live_refresh_after
            FROM agents a
            LEFT JOIN agent_live_state ls ON ls.agent_id = a.id
            """
        )
        agents = await cursor.fetchall()
        agent_ids = [row["id"] for row in agents]
        unread_map = await _get_unread_count_map(db, agent_ids)
        dispatch_map = await _get_dispatch_state_map(db, agent_ids)
        result = {}
        for row in agents:
            aid = row["id"]
            payload = _agent_record_to_dict(row, row["live_status"] if "live_status" in row.keys() else row["status"], unread_map.get(aid, 0), dispatch_map.get(aid))
            # Plan 5 Section C: read-path live-worker gate — see
            # _enforce_live_worker_gate for full rationale.
            payload = await _enforce_live_worker_gate(payload, db, settings, aid)
            result[aid] = payload
        return {"agents": result}
    finally:
        await db.close()


@router.post("/agents")
async def register_agent(req: AgentRegister, request: Request):
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        normalized_runtime = _normalize_runtime(req.runtime or "generic")
        normalized_session_mode = _normalize_session_mode(req.sessionMode or "resident")
        resolved_cwd = req.cwd or ""
        runtime_config = req.runtimeConfig or {}
        _validate_registration_cwd(
            agent_id=req.agentId,
            runtime=normalized_runtime,
            session_mode=normalized_session_mode,
            machine_id=req.machineId or "",
            cwd=resolved_cwd,
            runtime_config=runtime_config,
        )
        now = _now()
        tombstone = await _agent_tombstone(db, req.agentId)
        if tombstone and not req.restoreDeleted:
            if req.autoRegister:
                raise HTTPException(
                    410,
                    (
                        f"Agent '{req.agentId}' was intentionally removed at "
                        f"{tombstone['removed_at']}; auto re-registration is blocked."
                    ),
                )
            raise HTTPException(
                410,
                (
                    f"Agent '{req.agentId}' was intentionally removed. "
                    "Pass restoreDeleted=true to register this ID again."
                ),
            )
        if tombstone and req.restoreDeleted:
            # Tombstone-resurrection guard (2026-06-03). The bridge sets
            # restoreDeleted=true UNCONDITIONALLY on every auto/comms_register, so
            # a still-running bridge that predates the deletion would otherwise
            # clear the tombstone and resurrect a deliberately-removed agent
            # (it reappears in /api/v1/agents and the dashboard DM rail). Mirror
            # the environment forget-tombstone freshness check: only a GENUINE
            # fresh relaunch — a bridge whose bridgeStartedAt is NEWER than the
            # tombstone's removed_at — may restore. A passive auto re-register
            # from a bridge that launched BEFORE the deletion (or with no/older
            # bridgeStartedAt) keeps the agent deleted (410, tombstone untouched).
            #
            # An explicit, operator-initiated restore (restoreDeleted=true with
            # autoRegister=false — not a passive bridge beat) is preserved: a
            # deliberate operator bring-back still clears the tombstone.
            removed_at = _timestamp_sort_key(tombstone["removed_at"] if "removed_at" in tombstone.keys() else "")
            incoming_started = _timestamp_sort_key(req.bridgeStartedAt)
            relaunched = bool(incoming_started) and (not removed_at or incoming_started > removed_at)
            if req.autoRegister and not relaunched:
                raise HTTPException(
                    410,
                    (
                        f"Agent '{req.agentId}' was intentionally removed at "
                        f"{tombstone['removed_at']}; a lingering bridge cannot "
                        "resurrect it. Relaunch the agent to restore."
                    ),
                )
            await db.execute("DELETE FROM agent_tombstones WHERE agent_id = ?", (req.agentId,))
        existing = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        row = await existing.fetchone()
        bridge_id = (req.bridgeId or "").strip()
        terminal_id = str(req.terminalId or "").strip()
        # Mutual-exclusion collision guard (Task 4.1, 2026-05-30). One-driver
        # invariant: at most one driver per session at a time. If a process tries
        # to attach in a DIFFERENT session_mode than the one currently DRIVING
        # the session, reject with an actionable error so the operator switches
        # mode in the dashboard first (which releases the prior driver) rather
        # than silently colliding N wrappers / overwriting an active session.
        #
        # Scope: the guard fires ONLY on a cross-mode attach to a session that
        # is actively `driving`. Two cases are deliberately NOT hard-rejected
        # here because each is handled gracefully elsewhere, preserving the
        # invariant without an error:
        #   - SAME-mode re-attach/supersession by the same logical agent (a
        #     managed restart, or a second resident window) -> existing
        #     machine_id bridge supersession.
        #   - a RESIDENT registration against a DRIVING MANAGED agent -> the
        #     established `manualResidentCandidate` flow below parks the resident
        #     and returns `ownershipTransition=manual_switch_required` (it never
        #     lets the resident drive; the operator switches in the dashboard).
        # That leaves the genuinely-unhandled collision — a MANAGED registration
        # against a DRIVING RESIDENT session (which would otherwise silently
        # overwrite the live resident driver) — which is hard-rejected here.
        if row and not bool(req.restoreDeleted):
            existing_mode = _normalize_session_mode(row["session_mode"] or "resident")
            driver_state = str((row["driver_state"] if "driver_state" in row.keys() else "") or "idle").strip().lower()
            graceful_resident_candidate = (
                normalized_session_mode == "resident" and existing_mode == "managed"
            )
            if (
                driver_state == "driving"
                and existing_mode != normalized_session_mode
                and not graceful_resident_candidate
            ):
                resume_command = _resume_command_for(
                    row["runtime"] or normalized_runtime,
                    row["session_handle"] or "",
                    req.agentId,
                )
                detail = (
                    f"agent '{req.agentId}' is currently {existing_mode} — "
                    f"switch it to {normalized_session_mode} in the dashboard first, then run: "
                    f"{resume_command}"
                    if resume_command
                    else (
                        f"agent '{req.agentId}' is currently {existing_mode} — "
                        f"switch it to {normalized_session_mode} in the dashboard first."
                    )
                )
                raise HTTPException(409, detail)
        # Same-mode race guard (Phase 4, 2026-05-31). A fresh resident bridge of
        # the SAME mode, owned by a DIFFERENT bridge_id, is already driving this
        # identity — a second live wrapper would race it. Hard-reject (operator-
        # chosen) unless force=true: the operator deliberately takes over after
        # restarting the prior wrapper (wrappers surface this via the
        # AIFY_FORCE_REGISTER escape hatch). Stale prior bridges fall through and
        # are superseded normally (self-heal). Same-process periodic re-register
        # keeps its bridge_id and is excluded by `id != ?` in the helper.
        # NB: do NOT gate this on restoreDeleted — the bridge's auto-register
        # sends restoreDeleted=true unconditionally, so gating here would make
        # the guard dead in production. Restoring a tombstone is orthogonal: a
        # tombstoned agent has no live bridge to conflict with, so the freshness
        # check below simply finds nothing and the register proceeds.
        if row and bridge_id and not bool(getattr(req, "force", False)):
            settings_for_guard = await _load_settings(db)
            conflict = await _fresh_same_mode_bridge_conflict(
                db,
                agent_id=req.agentId,
                machine_id=req.machineId or "",
                new_bridge_id=bridge_id,
                session_mode=normalized_session_mode,
                lease_seconds=settings_for_guard.get("resident_lease_seconds", 150),
            )
            if conflict:
                seen_s = _iso_to_epoch((conflict["last_seen"] or ""))
                ago = int(max(0, time.time() - seen_s)) if seen_s else 0
                resume_command = _resume_command_for(
                    row["runtime"] or normalized_runtime,
                    row["session_handle"] or "",
                    req.agentId,
                )
                detail = (
                    f"agent '{req.agentId}' already has a LIVE {normalized_session_mode} "
                    f"bridge (seen {ago}s ago). Stop that instance first, or pass force=true "
                    f"(AIFY_FORCE_REGISTER=1) to take over."
                )
                if resume_command:
                    detail += f" To resume after taking over: {resume_command}"
                raise HTTPException(409, detail)
        managed_wrapper_child = bool(req.managedWrapperChild) or (
            normalized_session_mode == "managed"
            and bool(terminal_id)
            and normalized_runtime in _CHANNEL_CLAIM_RUNTIMES
        )
        if managed_wrapper_child and row:
            runtime_config = _merge_runtime_policy_for_wrapper_reregister(
                _json_loads_or(row["runtime_config"], {}),
                runtime_config,
            )
        model_value = req.model or ""
        if managed_wrapper_child and not model_value and row and "model" in row.keys():
            model_value = row["model"] or ""
        # Re-register is a full state refresh: sessionHandle and runtime_state come
        # from the new request only. Preserving them across re-register let stale
        # Codex thread IDs survive a fresh codex-aify start, which then made
        # thread/resume fail with AbsolutePathBuf or "no rollout found".
        # Reject unexpanded shell placeholders (e.g. "$HERMES_SESSION_ID") so a
        # literal never gets stored as the resume handle — see
        # _sanitize_session_handle.
        session_handle = _sanitize_session_handle(req.sessionHandle or "")
        existing_state = json.dumps(_runtime_state_with_handle(normalized_runtime, {}, session_handle))
        # Description is team-facing metadata that survives re-register when the
        # caller does not pass a new value. Passing "" explicitly clears it.
        if req.description is None:
            description_value = (row["description"] if row and "description" in row.keys() else "") or ""
        else:
            description_value = req.description
        capabilities = req.capabilities
        if capabilities is None:
            capabilities = _default_capabilities_for(normalized_runtime, normalized_session_mode, session_handle, runtime_config)
        console_terminal = None
        if terminal_id and normalized_session_mode == "resident":
            console_terminal = await (
                await db.execute(
                    """
                    SELECT *
                    FROM terminal_sessions
                    WHERE id = ?
                      AND agent_id = ?
                      AND status IN ('starting','attached','running','active','idle')
                    """,
                    (terminal_id, req.agentId),
                )
            ).fetchone()
        if console_terminal:
            existing_mode = _normalize_session_mode((row["session_mode"] if row else "") or "managed")
            existing_state = _json_loads_or((row["runtime_state"] if row else "") or "{}", {})
            existing_capabilities = (row["capabilities"] if row and "capabilities" in row.keys() else "") or json.dumps(capabilities or [])
            existing_runtime_config = (row["runtime_config"] if row and "runtime_config" in row.keys() else "") or json.dumps(runtime_config)
            next_state = _runtime_state_with_handle(normalized_runtime, existing_state, session_handle)
            next_state["consoleTerminal"] = {
                "terminalId": terminal_id,
                "bridgeId": bridge_id,
                "sessionHandle": session_handle,
                "at": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET role = ?,
                    name = ?,
                    cwd = ?,
                    runtime = ?,
                    machine_id = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    capabilities = ?,
                    runtime_config = ?,
                    runtime_state = ?,
                    status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    req.role,
                    req.name or req.agentId,
                    resolved_cwd,
                    normalized_runtime,
                    req.machineId or "",
                    session_handle,
                    session_handle,
                    existing_capabilities,
                    existing_runtime_config,
                    json.dumps(next_state),
                    "Dashboard Console PTY attached.",
                    now,
                    req.agentId,
                ),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'console',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    status = CASE WHEN status = 'cli-takeover' THEN 'running' ELSE status END,
                    ended_at = CASE WHEN status = 'cli-takeover' THEN NULL ELSE ended_at END,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    console_terminal["bridge_id"] or "",
                    terminal_id,
                    console_terminal["status"] or "attached",
                    session_handle,
                    session_handle,
                    now,
                    console_terminal["session_id"],
                ),
            )
            if bridge_id:
                await _record_bridge_registration(
                    db,
                    bridge_id=bridge_id,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=normalized_runtime,
                    session_mode="managed",
                    session_handle=session_handle,
                    terminal_id=terminal_id,
                    now=now,
                )
            await _invalidate_agent_live_state(db, req.agentId)
            await db.commit()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_registered", {
                    "agentId": req.agentId,
                    "role": req.role,
                    "runtime": normalized_runtime,
                    "machineId": req.machineId or "",
                    "sessionMode": existing_mode,
                    "ownershipTransition": "console_terminal_attached",
                })
            return {
                "ok": True,
                "agentId": req.agentId,
                "role": req.role,
                "status": req.status or "idle",
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "bridgeId": bridge_id,
                "sessionMode": existing_mode,
                "ownershipTransition": "console_terminal_attached",
            }
        fresh_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
        if bridge_id:
            fresh_state["bridgeInstanceId"] = bridge_id
        if normalized_session_mode == "resident":
            fresh_state["ownership"] = {
                "mode": "resident",
                "previousMode": _normalize_session_mode(row["session_mode"] or "managed") if row else "",
                "reason": "registered_cli",
                "at": now,
            }
        elif normalized_session_mode == "managed" and req.launchMode == "managed":
            fresh_state["ownership"] = {
                "mode": "managed",
                "previousMode": _normalize_session_mode(row["session_mode"] or "resident") if row else "",
                "reason": "registered_managed",
                "at": now,
            }
        # Plan 2 (2026-05-25) pi flip mechanics: pi-runtime no longer
        # supports a true resident session, but operators may still try
        # to register one (e.g. via legacy wrapper). Mark it pending-flip
        # so _drain_and_flip_pi_resident_agents (Task 17) can migrate it
        # to managed once any active runs drain. Once flipped, the agent
        # row's session_mode becomes "managed" and capabilities are
        # recomputed from PiAdapter (supports_resident=False).
        if normalized_runtime == "pi" and normalized_session_mode == "resident":
            fresh_state["pi_resident_pending_flip"] = True
        existing_state = json.dumps(fresh_state)
        if row and normalized_session_mode == "resident" and _normalize_session_mode(row["session_mode"] or "resident") == "managed":
            active_run = await _get_blocking_active_run(db, req.agentId)
            existing_state_dict = _json_loads_or(row["runtime_state"], {})
            existing_state_dict.pop("pendingResidentTakeover", None)
            existing_state_dict["manualResidentCandidate"] = {
                "bridgeId": bridge_id,
                "machineId": req.machineId or "",
                "runtime": normalized_runtime,
                "sessionHandle": session_handle,
                "runtimeConfig": runtime_config,
                "capabilities": capabilities or [],
                "cwd": resolved_cwd,
                "launchMode": req.launchMode or "detached",
                "registeredAt": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET runtime_state = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(existing_state_dict),
                    (
                        f"Resident CLI registered, but agent remains managed. Use Switch to resident when ready."
                        + (f" Active run {active_run.get('runId') or ''} is still running." if active_run else "")
                    ),
                    now,
                    req.agentId,
                ),
            )
            if session_handle:
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET session_handle = ?,
                        telemetry = CASE
                            WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                            ELSE telemetry
                        END,
                        last_seen = ?
                    WHERE id = (
                        SELECT id
                        FROM agent_sessions
                        WHERE agent_id = ?
                          AND runtime = ?
                          AND status = 'cli-takeover'
                        ORDER BY last_seen DESC
                        LIMIT 1
                    )
                    """,
                    (
                        session_handle,
                        json.dumps({"registeredHandle": _runtime_state_with_handle(normalized_runtime, {}, session_handle)}),
                        now,
                        req.agentId,
                        normalized_runtime,
                    ),
                )
            if bridge_id:
                await _record_bridge_registration(
                    db,
                    bridge_id=bridge_id,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=normalized_runtime,
                    session_mode="resident",
                    session_handle=session_handle,
                    terminal_id=terminal_id,
                    now=now,
                )
            await _invalidate_agent_live_state(db, req.agentId)
            await db.commit()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_registered", {
                    "agentId": req.agentId,
                    "role": req.role,
                    "runtime": normalized_runtime,
                    "machineId": req.machineId or "",
                    "sessionMode": "managed",
                    "residentBridgeId": bridge_id,
                })
            return {
                "ok": True,
                "agentId": req.agentId,
                "role": req.role,
                "status": row["status"] or "active",
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "bridgeId": bridge_id,
                "sessionMode": "managed",
                "ownershipTransition": "manual_switch_required",
                # Task 4.1: the takeover command the operator runs after flipping
                # the agent to resident in the dashboard (one-driver invariant).
                "resumeCommand": _resume_command_for(normalized_runtime, session_handle, req.agentId),
                "blockedByRun": active_run,
            }
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note, runtime, machine_id,
                launch_mode, session_mode, session_handle, managed_by, capabilities,
                runtime_config, runtime_state, driver_state, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                name = excluded.name,
                cwd = excluded.cwd,
                model = excluded.model,
                description = excluded.description,
                instructions = excluded.instructions,
                status = excluded.status,
                status_note = excluded.status_note,
                runtime = excluded.runtime,
                machine_id = excluded.machine_id,
                launch_mode = excluded.launch_mode,
                session_mode = excluded.session_mode,
                session_handle = excluded.session_handle,
                managed_by = excluded.managed_by,
                capabilities = excluded.capabilities,
                runtime_config = excluded.runtime_config,
                runtime_state = excluded.runtime_state,
                driver_state = excluded.driver_state,
                last_seen = excluded.last_seen
            """,
            (
                req.agentId, req.role, req.name or req.agentId, resolved_cwd, model_value,
                description_value, req.instructions or "", req.status or "idle",
                (row["status_note"] if row and "status_note" in row.keys() else "") or "",
                normalized_runtime,
                req.machineId or "", req.launchMode or "detached",
                normalized_session_mode, session_handle, req.managedBy or "",
                json.dumps(capabilities or []), json.dumps(runtime_config),
                existing_state,
                # One-driver FSM: an attaching process carrying a bridge_id is a
                # live driver for this session -> mark driving. A metadata-only
                # (re)register without a bridge keeps the prior driver_state.
                ("driving" if bridge_id else (str((row["driver_state"] if row and "driver_state" in row.keys() else "") or "idle"))),
                row["registered_at"] if row and row["registered_at"] else now, now
            )
        )
        if session_handle:
            app_server_url = ""
            if isinstance(runtime_config, dict):
                app_server_url = str(runtime_config.get("appServerUrl") or "").strip()
            session_runtime_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
            await db.execute(
                """
                UPDATE agent_sessions
                SET session_handle = ?,
                    app_server_url = CASE WHEN ? != '' THEN ? ELSE app_server_url END,
                    last_seen = ?,
                    capabilities = CASE
                        WHEN COALESCE(NULLIF(capabilities, ''), '{}') = '{}' THEN ?
                        ELSE capabilities
                    END,
                    telemetry = CASE
                        WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                        ELSE telemetry
                    END
                WHERE id = (
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                    ORDER BY last_seen DESC
                    LIMIT 1
                )
                """,
                (
                    session_handle,
                    app_server_url,
                    app_server_url,
                    now,
                    json.dumps({"persistent": True, "nativeResume": True, "bridgeResume": True, "cliAttach": True}),
                    json.dumps({"registeredHandle": session_runtime_state}),
                    req.agentId,
                    normalized_runtime,
                ),
            )
        if bridge_id:
            await _record_bridge_registration(
                db,
                bridge_id=bridge_id,
                agent_id=req.agentId,
                machine_id=req.machineId or "",
                runtime=normalized_runtime,
                session_mode=normalized_session_mode,
                session_handle=session_handle,
                terminal_id=terminal_id,
                managed_wrapper_child=managed_wrapper_child,
                now=now,
            )
        await _invalidate_agent_live_state(db, req.agentId)
        # Universal rule: when a *-aify wrapper registers an agent as
        # resident, the operator's real terminal owns it. ANY managed
        # wrapper PTY that exists for this agent must be torn down at
        # that moment — no time-based detection, just the resident-
        # register event itself triggers it. Mark active terminal_sessions
        # as stopped with a clear reason; clear the agent_session
        # terminal_id binding so the dashboard stops displaying a ghost
        # console; send a 'stop' terminal_control to the owning bridge
        # so the underlying PTY process is killed if still alive.
        if normalized_session_mode == "resident":
            stale_terminals = await (
                await db.execute(
                    """
                    SELECT id, environment_id, bridge_id
                    FROM terminal_sessions
                    WHERE agent_id = ?
                      AND status IN ('starting','attached','running','active','idle','recovering')
                      AND (? = '' OR id != ?)
                    """,
                    (req.agentId, terminal_id, terminal_id),
                )
            ).fetchall()
            for term in stale_terminals:
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET status = 'stopped',
                        stopped_at = ?,
                        updated_at = ?,
                        error = COALESCE(NULLIF(error, ''), 'superseded_by_resident_takeover')
                    WHERE id = ?
                    """,
                    (now, now, term["id"]),
                )
                await _append_terminal_event(
                    db,
                    term["id"],
                    "superseded_by_resident_takeover",
                    json.dumps({
                        "agentId": req.agentId,
                        "residentBridge": bridge_id,
                        "newSessionMode": "resident",
                    }),
                )
                # Best-effort kill: enqueue 'stop' so the owning bridge
                # tears down the wrapper subprocess if still alive. If
                # the bridge is dead, the row is already marked stopped
                # so it doesn't matter that the control is never claimed.
                await _append_terminal_control(
                    db,
                    terminal_id=term["id"],
                    environment_id=term["environment_id"] or "",
                    bridge_id=term["bridge_id"] or "",
                    action="stop",
                    requested_by="resident-takeover",
                    body="",
                )
            if stale_terminals:
                # Clear agent_sessions.terminal_id binding for sessions
                # that pointed at any of the just-stopped terminals so
                # the dashboard stops rendering a ghost Console.
                stopped_ids = [t["id"] for t in stale_terminals]
                placeholders = ",".join(["?"] * len(stopped_ids))
                await db.execute(
                    f"""
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = ''
                    WHERE agent_id = ?
                      AND terminal_id IN ({placeholders})
                    """,
                    (req.agentId, *stopped_ids),
                )
            await _upsert_resident_agent_session(
                db,
                agent_id=req.agentId,
                runtime=normalized_runtime,
                workspace=resolved_cwd,
                machine_id=req.machineId or "",
                session_handle=session_handle,
                runtime_config=runtime_config,
                bridge_id=bridge_id,
                capabilities=capabilities or [],
                now=now,
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_registered", {
                "agentId": req.agentId,
                "role": req.role,
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "sessionMode": normalized_session_mode,
            })
        return {
            "ok": True,
            "agentId": req.agentId,
            "role": req.role,
            "status": req.status or "idle",
            "runtime": normalized_runtime,
            "machineId": req.machineId or "",
            "bridgeId": bridge_id,
            "sessionMode": normalized_session_mode,
        }
    finally:
        await db.close()


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        await _refresh_expired_agent_live_states(db, settings=settings, agent_ids=[agent_id])
        cursor = await db.execute(
            """
            SELECT a.*, ls.status AS live_status, ls.reason AS live_reason, ls.refresh_after AS live_refresh_after
            FROM agents a
            LEFT JOIN agent_live_state ls ON ls.agent_id = a.id
            WHERE a.id = ?
            """,
            (agent_id,),
        )
        row = await cursor.fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        unread_map = await _get_unread_count_map(db, [agent_id])
        dispatch_map = await _get_dispatch_state_map(db, [agent_id])
        payload = _agent_record_to_dict(row, row["live_status"] if "live_status" in row.keys() else row["status"], unread_map.get(agent_id, 0), dispatch_map.get(agent_id))
        # Plan 5 Section C: read-path live-worker gate — see
        # _enforce_live_worker_gate for full rationale.
        payload = await _enforce_live_worker_gate(payload, db, settings, agent_id)
        return {"ok": True, "agentId": agent_id, "agent": payload}
    finally:
        await db.close()


_CONSOLE_TAIL_MAX_LINES = 200
_CONSOLE_TAIL_MAX_BYTES = 16 * 1024


async def _resolve_live_console_terminal(db, agent_id: str):
    """Resolve an agent's LIVE console terminal row from its runtime_state.

    Returns the (non-terminated) terminal_sessions row pointed at by
    runtime_state.consoleTerminal.terminalId (managed claude) or
    runtime_state.virtualTerminalId (pi/hermes virtual), or None when the
    agent has no live console. Resolution is agent-scoped on purpose: callers
    can only reach a terminal *through* the agent, never by arbitrary id.
    """
    agent_row = await (
        await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))
    ).fetchone()
    if not agent_row:
        return None
    runtime_state = _json_loads_or(agent_row["runtime_state"], {})
    if not isinstance(runtime_state, dict):
        return None
    terminal_id = ""
    console_terminal = runtime_state.get("consoleTerminal")
    if isinstance(console_terminal, dict):
        terminal_id = str(console_terminal.get("terminalId") or "").strip()
    if not terminal_id:
        terminal_id = str(runtime_state.get("virtualTerminalId") or "").strip()
    if not terminal_id:
        return None
    terminal = await (
        await db.execute(
            "SELECT * FROM terminal_sessions WHERE id = ? AND agent_id = ?",
            (terminal_id, agent_id),
        )
    ).fetchone()
    if not terminal:
        return None
    status = str(terminal["status"] or "").strip().lower()
    if status in _TERMINAL_END_STATUSES:
        return None
    return terminal


@router.get("/agents/{agent_id}/console")
async def get_agent_console(agent_id: str, lines: int = 40):
    """Read the tail of an agent's live console output (read-only).

    Side-effect-free: never starts a worker. Resolves the agent's live console
    terminal via runtime_state; if none, returns {ok, live:false, message}.
    """
    db = await get_db()
    try:
        terminal = await _resolve_live_console_terminal(db, agent_id)
        if not terminal:
            agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
            if not agent_row:
                raise HTTPException(404, f"Agent '{agent_id}' not found")
            return {
                "ok": True,
                "live": False,
                "message": f"{agent_id} has no live console (it lazy-starts on a message).",
            }
        # Drain any buffered output for this terminal so the tail is current,
        # then re-read the row to pick up the flushed bytes.
        await TERMINAL_OUTPUT_WRITES.flush_terminal(terminal["id"])
        terminal = await (
            await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))
        ).fetchone()
        tail_lines = max(1, min(int(lines or 40), _CONSOLE_TAIL_MAX_LINES))
        full_output = (terminal["output"] if "output" in terminal.keys() else "") or ""
        selected = full_output.splitlines()[-tail_lines:]
        output = "\n".join(selected)
        if len(output.encode("utf-8", "ignore")) > _CONSOLE_TAIL_MAX_BYTES:
            output = output.encode("utf-8", "ignore")[-_CONSOLE_TAIL_MAX_BYTES:].decode("utf-8", "ignore")
        return {
            "ok": True,
            "live": True,
            "terminalId": terminal["id"],
            "status": terminal["status"] or "",
            "lines": len(selected),
            "output": output,
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/console/input")
async def post_agent_console_input(agent_id: str, req: AgentConsoleInputRequest, request: Request):
    """Send input (keystrokes/text) into an agent's live console. Audited.

    SAFETY: the caller (`from`) must be a registered agent; the input is
    recorded against that caller in both the terminal control's requested_by
    and an `agent_console_input` audit event. Callers can only target the
    agent's own resolved console terminal — never an arbitrary terminal id.
    Managed agents only (v1).
    """
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT id, runtime, session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        caller = str(req.from_ or "").strip()
        if not caller:
            raise HTTPException(400, "console input requires a `from` caller (the requesting agent id)")
        caller_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (caller,))).fetchone()
        if not caller_row:
            raise HTTPException(403, f"caller '{caller}' is not a registered agent")

        settings = await _load_settings(db)
        terminal = await _resolve_live_console_terminal(db, agent_id)
        if not terminal:
            # Best-effort lazy-autostart the SAME way dispatch does so the
            # visible-TUI requirement is preserved. If nothing can be started
            # (no live session / offline env), return the clear message.
            started = await _ensure_managed_pty_for_dispatch(
                db,
                agent_id,
                runtime=str(agent_row["runtime"] or ""),
                settings=settings,
                requested_by=caller,
            )
            await db.commit()
            if not started:
                return {
                    "ok": False,
                    "live": False,
                    "message": f"{agent_id} has no live console; send a message to start it first.",
                }
            # Re-resolve via runtime_state (autostart publishes the pointer).
            terminal = await _resolve_live_console_terminal(db, agent_id)
            if not terminal:
                # The freshly-started terminal row exists but its runtime_state
                # pointer may not be the consoleTerminal shape yet (e.g. the
                # `starting` row from _ensure_managed_pty_for_dispatch). Use it
                # directly — it is agent-scoped (the helper only returns this
                # agent's own session terminal).
                started_id = started["terminal_id"] if "terminal_id" in started.keys() else started["id"]
                terminal = await (
                    await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (started_id,))
                ).fetchone()
            if not terminal:
                return {
                    "ok": False,
                    "live": False,
                    "message": f"{agent_id} has no live console; send a message to start it first.",
                }

        text = str(req.text or "")
        body = text + ("\r" if (req.enter is None or req.enter) else "")
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal["id"],
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="input",
            requested_by=caller,
            body=body,
        )
        await _append_terminal_event(
            db,
            terminal["id"],
            "agent_console_input",
            json.dumps({"from": caller, "controlId": control_id, "bytes": len(body)}),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal["id"], "action": "input"})
        return {
            "ok": True,
            "live": True,
            "terminalId": terminal["id"],
            "controlId": control_id,
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/rename")
async def rename_agent(agent_id: str, req: AgentRenameRequest, request: Request):
    validate_name(agent_id, "agent ID")
    new_agent_id = str(req.newAgentId or "").strip()
    validate_name(new_agent_id, "new agent ID")
    if new_agent_id == agent_id:
        return {"ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": False}

    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        existing = await (await db.execute("SELECT id FROM agents WHERE id = ?", (new_agent_id,))).fetchone()
        if existing:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" already exists')
        tombstone = await _agent_tombstone(db, new_agent_id)
        if tombstone:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" was intentionally removed before; clear that ID before reusing it')

        now = _now()
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note,
                runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                capabilities, runtime_config, runtime_state, registered_at, last_seen
            )
            SELECT ?, role, CASE WHEN name = id THEN ? ELSE name END, cwd, model, description,
                   instructions, status, status_note, runtime, machine_id, launch_mode,
                   session_mode, session_handle, managed_by, capabilities, runtime_config,
                   runtime_state, registered_at, ?
            FROM agents
            WHERE id = ?
            """,
            (new_agent_id, new_agent_id, now, agent_id),
        )
        for table, column in (
            ("agent_sessions", "agent_id"),
            ("spawn_specs", "agent_id"),
            ("spawn_requests", "agent_id"),
            ("bridge_instances", "agent_id"),
            ("read_receipts", "agent_id"),
            ("channel_members", "agent_id"),
        ):
            await db.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET to_agent = ? WHERE to_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE shared_artifacts SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET target_agent = ? WHERE target_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_controls SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE channels SET created_by = ? WHERE created_by = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE agents SET managed_by = ? WHERE managed_by = ?", (new_agent_id, agent_id))
        await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await db.execute(
            """
            INSERT OR REPLACE INTO agent_tombstones (agent_id, removed_at, removed_by, bridge_id, reason)
            VALUES (?,?,?,?,?)
            """,
            (agent_id, now, req.requestedBy or "dashboard", "", f"renamed_to:{new_agent_id}"),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_renamed", {"oldAgentId": agent_id, "newAgentId": new_agent_id})
        return {"ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": True}
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    finally:
        await db.close()


@router.post("/agents/{agent_id}/environment")
async def assign_agent_environment(agent_id: str, req: AgentEnvironmentAssignRequest, request: Request):
    validate_name(agent_id, "agent ID")
    environment_id = str(req.environmentId or "").strip()
    if not environment_id:
        raise HTTPException(400, "environmentId is required")

    db = await get_db()
    try:
        agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await agent_cursor.fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{environment_id}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment_id}" is {environment.get("status") or "unknown"}, not online')

        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "generic")
        if not _runtime_capability_for_environment(environment, runtime):
            raise HTTPException(400, f'Environment "{environment_id}" does not advertise runtime "{runtime}"')
        workspace, workspace_root = _workspace_for_environment(environment, req.workspace, agent["cwd"] or "")
        settings = await _load_settings(db)
        model = str(req.model if req.model is not None else (agent["model"] or "")).strip()
        if not model:
            if runtime == "codex":
                model = str(settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"])).strip()
            elif runtime == "claude-code":
                model = str(settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"])).strip()
            elif runtime == "pi":
                model = str(settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"])).strip()
        existing_runtime_config = _json_loads_or(agent["runtime_config"], {})
        requested_runtime_config = req.runtimeConfig or {}
        runtime_config = {**existing_runtime_config, **requested_runtime_config}
        if runtime == "codex" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]).strip()}
        elif runtime == "claude-code" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]).strip()}
        elif runtime == "pi" and not str(runtime_config.get("effort") or runtime_config.get("thinking") or "").strip():
            pi_effort = str(settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]).strip()
            if pi_effort:
                runtime_config = {**runtime_config, "effort": pi_effort}
        now = _now()
        previous_runtime = _normalize_runtime(agent["runtime"] or runtime)
        latest_session = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY
                CASE WHEN COALESCE(NULLIF(session_handle, ''), '') != '' THEN 0 ELSE 1 END,
                last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )).fetchone()
        latest_session_handle = str((latest_session["session_handle"] if latest_session else "") or "").strip()
        agent_runtime_state = _json_loads_or(agent["runtime_state"], {})
        state_handle = _runtime_handle_from_state(previous_runtime, agent_runtime_state)
        preserve_handle = ""
        if previous_runtime == runtime:
            preserve_handle = str(agent["session_handle"] or latest_session_handle or state_handle or "").strip()
        preserved_runtime_state = _runtime_state_with_handle(runtime, {}, preserve_handle)

        spec_cursor = await db.execute(
            "SELECT * FROM spawn_specs WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        )
        spec = await spec_cursor.fetchone()
        if spec:
            spec_id = spec["id"]
            await db.execute(
                """
                UPDATE spawn_specs
                SET environment_id = ?, runtime = ?, workspace = ?, model = ?, metadata = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    environment_id,
                    runtime,
                    workspace,
                    model,
                    json.dumps({**_json_loads_or(spec["metadata"], {}), **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    agent_id,
                ),
            )
        else:
            spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
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
                    runtime,
                    workspace,
                    model,
                    "",
                    "managed-warm",
                    "",
                    agent["instructions"] or "",
                    "{}",
                    "[]",
                    "{}",
                    "{}",
                    "{}",
                    json.dumps({"createdBy": req.requestedBy or "dashboard", "assignedFromDashboard": True, **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    now,
                ),
            )

        await db.execute(
            """
            UPDATE agent_sessions
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                session_handle = ?,
                spawn_spec_id = COALESCE(NULLIF(spawn_spec_id, ''), ?),
                status = CASE WHEN status IN ('starting','running','recovering','restarting') THEN 'lost' ELSE status END,
                ended_at = CASE WHEN status IN ('starting','running','recovering','restarting') THEN COALESCE(ended_at, ?) ELSE ended_at END,
                last_seen = ?
            WHERE agent_id = ?
            """,
            (environment_id, runtime, workspace, preserve_handle, spec_id, now, now, agent_id),
        )
        session_cursor = await db.execute(
            "SELECT id FROM agent_sessions WHERE agent_id = ? ORDER BY last_seen DESC LIMIT 1",
            (agent_id,),
        )
        existing_session = await session_cursor.fetchone()
        if not existing_session:
            session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode,
                    owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
                    process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    "managed-warm",
                    "managed",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    preserve_handle,
                    "",
                    spec_id,
                    None,
                    json.dumps({"persistent": True, "nativeResume": bool(preserve_handle), "bridgeResume": True, "adopted": True}),
                    "{}",
                    "stopped",
                    now,
                    now,
                    now,
                ),
            )
        await db.execute(
            """
            UPDATE spawn_requests
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                workspace_root = ?,
                updated_at = ?
            WHERE agent_id = ?
              AND status IN ('queued','claimed','starting')
            """,
            (environment_id, runtime, workspace, workspace_root, now, agent_id),
        )
        capabilities = _default_capabilities_for(runtime, "managed", preserve_handle, runtime_config)
        await db.execute(
            """
            UPDATE agents
            SET cwd = ?,
                model = ?,
                runtime = ?,
                machine_id = ?,
                launch_mode = 'none',
                session_mode = 'managed',
                session_handle = ?,
                capabilities = ?,
                runtime_config = ?,
                runtime_state = ?,
                status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                last_seen = ?
            WHERE id = ?
            """,
            (
                workspace,
                model,
                runtime,
                _normalize_machine_id(environment.get("machineId")),
                preserve_handle,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                json.dumps(preserved_runtime_state),
                now,
                agent_id,
            ),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_environment_assigned", {"agentId": agent_id, "environmentId": environment_id})
        return {
            "ok": True,
            "agentId": agent_id,
            "environmentId": environment_id,
            "runtime": runtime,
            "workspace": workspace,
            "spawnSpecId": spec_id,
        }
    finally:
        await db.close()


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        # fix/hermes-leak P2 (REMOVE): for a MANAGED agent, tear the triad down by
        # signalling the bridge BEFORE the agent record is gone. We cannot use a
        # terminal_control here: deleting the agent cascades agents → agent_sessions
        # → terminal_sessions → terminal_controls, so any control emitted in this
        # request is wiped by the same delete. Instead REMOVE drives the triad reap
        # through the SAME agent-control STOP path (status=stopped + the bridge's
        # managed-hermes terminal stop reaps the triad), committed in its own
        # transaction, THEN tombstones. This makes REMOVE = STOP-then-tombstone, so
        # the surviving stop control (claimed before the tombstone delete) carries
        # the triad-reap. Resident agents are skipped (operator's own session).
        cursor = await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))
        agent_row = await cursor.fetchone()
        managed = bool(agent_row) and _normalize_session_mode(agent_row["session_mode"] or "resident") == "managed"
        if managed:
            now = _now()
            await db.execute(
                "UPDATE agents SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ? WHERE id = ?",
                ("Removed from dashboard; tearing down managed session.", now, agent_id),
            )
            await _request_stop_agent_terminals(
                db, agent_id, requested_by="api", now=now, reap_triad=True,
            )
            await db.commit()
        deleted = await _remove_agent_record(
            db,
            agent_id,
            removed_by="api",
            reason="delete_agent",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_removed", {"agentId": agent_id})
        return {"ok": deleted > 0, "agentId": agent_id}
    finally:
        await db.close()


# Body sentinel prefix on a `stop` terminal control that must ALSO reap the
# MANAGED-HERMES triad (gateway host + delivery loop + daemon), not just the PTY
# (fix/hermes-leak P2). Used by REMOVE: after the agent row is deleted the claim
# can no longer resolve session_mode, so the sentinel carries the triad-reap
# intent forward. The bridge honors runtime=hermes + (sessionMode=managed OR this
# sentinel). The human-readable suffix is preserved for the console.
_REAP_TRIAD_BODY_SENTINEL = "__aify_reap_triad__"


async def _request_stop_agent_terminals(
    db, agent_id: str, *, requested_by: str, now: str, reap_triad: bool = False,
) -> int:
    """Stop an agent's live MANAGED terminals — an operator Stop must kill the
    running console/TUI, since aify-comms is the lifecycle driver for managed
    sessions (operator-reported 2026-05-31: Stop interrupted the run + marked the
    agent stopped but left the host TUI running). Appends a 'stop' terminal
    control (the bridge's terminal-control poll reaps the PTY) and marks the
    terminal 'stopping'. Skips synthetic (vterm_) and already terminal-state
    rows. Returns the number of terminals signaled.

    reap_triad (fix/hermes-leak P2): stamp the body sentinel so a MANAGED-HERMES
    stop also tears down the detached triad (gateway/loop/daemon) on the bridge,
    even when the agent row is already gone (REMOVE) and session_mode can't be
    resolved at claim time."""
    cursor = await db.execute(
        """
        SELECT id, environment_id, bridge_id, session_id FROM terminal_sessions
        WHERE agent_id = ?
          AND id NOT LIKE 'vterm_%'
          AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering', 'stopping')
        """,
        (agent_id,),
    )
    stop_body = "Agent stopped from dashboard."
    if reap_triad:
        stop_body = f"{_REAP_TRIAD_BODY_SENTINEL} {stop_body}"
    count = 0
    for t in await cursor.fetchall():
        await _append_terminal_control(
            db,
            terminal_id=t["id"],
            environment_id=t["environment_id"] or "",
            bridge_id=t["bridge_id"] or "",
            action="stop",
            requested_by=requested_by,
            body=stop_body,
        )
        await db.execute(
            "UPDATE terminal_sessions SET status = 'stopping', updated_at = ? WHERE id = ?",
            (now, t["id"]),
        )
        if t["session_id"]:
            await db.execute(
                "UPDATE agent_sessions SET terminal_status = 'stopping', last_seen = ? WHERE id = ?",
                (now, t["session_id"]),
            )
        count += 1
    return count


@router.post("/agents/{agent_id}/control")
async def control_agent(agent_id: str, req: AgentControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"interrupt", "stop", "resume"}:
        raise HTTPException(400, f'Unsupported agent control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        now = _now()
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if action in {"interrupt", "stop"}:
            if active_run:
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=req.from_agent or "dashboard",
                    action="interrupt",
                    body=req.body or f"Agent {action} requested from dashboard.",
                )
            elif action == "interrupt":
                raise HTTPException(409, f'Agent "{agent_id}" has no active run to interrupt')

        cancelled_queued = 0
        if action == "stop":
            queued_cursor = await db.execute(
                "SELECT id FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
                (agent_id,),
            )
            queued_rows = await queued_cursor.fetchall()
            for row in queued_rows:
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id = ?",
                    (f'Agent "{agent_id}" was stopped from the dashboard before the run could start.', now, row["id"]),
                )
                await _append_dispatch_event(db, row["id"], "agent_stopped", "Agent stopped from dashboard")
                cancelled_queued += 1
            stop_note = "Stopped from dashboard. Resume to allow wake/dispatch again."
            if _normalize_session_mode(agent["session_mode"] or "resident") == "resident":
                stop_note = "Resident session stop requested from dashboard; live bridge should terminate the CLI host."
            await db.execute(
                """
                UPDATE agents
                SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ?
                WHERE id = ?
                """,
                (stop_note, now, agent_id),
            )
            # Kill the managed console/TUI too — aify-comms is the lifecycle driver
            # for managed sessions, so Stop must tear down the running terminal
            # instead of leaving an abandoned TUI (operator-reported 2026-05-31).
            # Resident windows are the operator's OWN process; the bridge teardown
            # handles those (see stop_note), so this is managed-only.
            if _normalize_session_mode(agent["session_mode"] or "resident") == "managed":
                await _request_stop_agent_terminals(
                    db, agent_id, requested_by=req.from_agent or "dashboard", now=now,
                )
        elif action == "resume":
            await db.execute(
                """
                UPDATE agents
                SET status = 'idle', status_note = '', launch_mode = CASE WHEN launch_mode = 'none' THEN 'detached' ELSE launch_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (now, agent_id),
            )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_control_requested",
                {"agentId": agent_id, "action": action, "controlId": control_id, "cancelledQueued": cancelled_queued},
            )
        await _broadcast_agent_status(ws, db, agent_id)
        return {
            "ok": True,
            "agentId": agent_id,
            "action": action,
            "controlId": control_id,
            "cancelledQueued": cancelled_queued,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentStatusUpdate, request: Request):
    db = await get_db()
    try:
        note = getattr(req, 'note', None) or ''
        status_val = f"{req.status}: {note}" if note else req.status
        cursor = await db.execute(
            "UPDATE agents SET status = ?, status_note = ?, last_seen = ? WHERE id = ?",
            (req.status, note, _now(), agent_id)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        ws = await _get_ws(request)
        if ws:
            # Keep req.status authoritative (operator-set), enrich with the note
            # so dashboards can render it on the agent's row without a refetch.
            await ws.broadcast("agent_status", {"agentId": agent_id, "status": req.status, "statusNote": note})
        return {"ok": True, "agentId": agent_id, "status": status_val, "statusRaw": req.status, "statusNote": note}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/session-handle")
async def update_agent_session_handle(agent_id: str, req: AgentSessionHandleUpdate, request: Request):
    validate_name(agent_id, "agent ID")
    # Drop unexpanded shell placeholders ("$HERMES_SESSION_ID", "${VAR}") so a
    # literal is never stored as the resume handle — see _sanitize_session_handle.
    session_handle = _sanitize_session_handle(req.sessionHandle)
    if len(session_handle) > 512:
        raise HTTPException(400, "sessionHandle must be 512 characters or fewer")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        # ── Sticky session identity + new-id guard (governance, 2026-05-30) ──
        # The bridge heartbeat (session-handle-heartbeat.js, requestedBy=
        # "bridge-heartbeat") continuously reports the runtime's *discovered*
        # session id. We must NOT silently overwrite the persisted handle when
        # that discovered id DRIFTS from what we already pinned — a drift is the
        # observable symptom of a split (agent landed on a fresh id) or a merge
        # (two agents converging on one id). Instead we park the proposed id in
        # `pending_session_id`, flag the agent `session-changed`, and KEEP
        # delivery pointed at the old handle until the operator resolves it.
        #
        # Scope is deliberately narrow so we never break the existing flows:
        #   • First-id auto-accept — no persisted handle yet → accept (current).
        #   • Same id re-reported → no-op (no pending, no churn).
        #   • Clearing (empty handle) → allowed (heal paths clear poisoned ids).
        #   • Deliberate operator re-pin (any other requestedBy, e.g. dashboard
        #     manual set, console attach) → unguarded, as before.
        #   • Re-register (POST /agents) is a separate write site and remains a
        #     full state refresh — it is NOT routed through here.
        requested_by = str(req.requestedBy or "").strip()
        persisted_handle = str(row["session_handle"] or "").strip()

        # ── Cross-agent collision guard (root-cause fix, 2026-05-31) ──
        # A runtime session id must be owned by at most ONE live agent. Never let
        # agent X ADOPT a session id that a DIFFERENT LIVE agent already owns —
        # the resident<->managed invariant. (Incident: graph-tech-lead adopted
        # comms-tech-lead's live resident id 651b895f at 06:07; the kill-prior
        # reaper then turned that collision fatal.) This fires for ANY source
        # (capture, heartbeat, manual set) and covers the first-id case too. Park
        # the colliding id as `pending_session_id` and KEEP this agent's own
        # handle (empty stays empty → the agent launches fresh and captures its
        # OWN id, which won't collide). A stale/dead owner is NOT a collision
        # (the id is free to reassign) — _session_handle_live_owner gates on
        # heartbeat freshness.
        if session_handle and session_handle != persisted_handle:
            _settings_g = await _load_settings(db)
            _owner = await _session_handle_live_owner(
                db, session_handle, exclude_agent_id=agent_id,
                lease_seconds=_settings_g.get("resident_lease_seconds", 150),
            )
            if _owner:
                _note = (
                    f"session-collision: reported id '{session_handle}' is already owned by live "
                    f"agent '{_owner['agentId']}' ({_owner['sessionMode']}); kept own handle. "
                    "Two live agents must not share one session id."
                )
                await db.execute(
                    "UPDATE agents SET pending_session_id = ?, status_note = ?, last_seen = ? WHERE id = ?",
                    (session_handle, _note, now, agent_id),
                )
                await db.commit()
                updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                settings = await _load_settings(db)
                status = await _compute_agent_status(updated, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
                dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
                ws = await _get_ws(request)
                if ws:
                    await ws.broadcast("agent_session_changed", {
                        "agentId": agent_id,
                        "sessionHandle": persisted_handle,
                        "pendingSessionId": session_handle,
                        "collisionWith": _owner["agentId"],
                    })
                return {
                    "ok": True,
                    "agentId": agent_id,
                    "state": "session-collision",
                    "collisionWith": _owner["agentId"],
                    # Delivery keeps targeting THIS agent's own handle; the
                    # colliding id is NOT adopted.
                    "sessionHandle": persisted_handle,
                    "pendingSessionId": session_handle,
                    "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
                }

        if (
            requested_by == "bridge-heartbeat"
            and session_handle
            and persisted_handle
            and session_handle != persisted_handle
        ):
            await db.execute(
                """
                UPDATE agents
                SET pending_session_id = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    session_handle,
                    (
                        f"session-changed: reported id '{session_handle}' differs from "
                        f"pinned '{persisted_handle}'. Confirm new or keep current."
                    ),
                    now,
                    agent_id,
                ),
            )
            await db.commit()
            updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
            settings = await _load_settings(db)
            status = await _compute_agent_status(updated, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
            dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_session_changed", {
                    "agentId": agent_id,
                    "sessionHandle": persisted_handle,
                    "pendingSessionId": session_handle,
                })
            return {
                "ok": True,
                "agentId": agent_id,
                "state": "session-changed",
                # Delivery still targets the OLD (persisted) handle — unchanged.
                "sessionHandle": persisted_handle,
                "pendingSessionId": session_handle,
                "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
            }

        runtime = _normalize_runtime(row["runtime"] or "generic")
        session_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime_config = _json_loads_or(row["runtime_config"], {})
        runtime_state = _runtime_state_replacing_handle(runtime, row["runtime_state"], session_handle)
        capabilities = _default_capabilities_for(runtime, session_mode, session_handle, runtime_config)
        registered_handle = _runtime_state_with_handle(runtime, {}, session_handle)
        await db.execute(
            """
            UPDATE agents
            SET session_handle = ?,
                pending_session_id = '',
                runtime_state = ?,
                capabilities = ?,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                session_handle,
                json.dumps(runtime_state),
                json.dumps(capabilities),
                f"Session handle set by {req.requestedBy or 'operator'}." if session_handle else f"Session handle cleared by {req.requestedBy or 'operator'}.",
                now,
                agent_id,
            ),
        )
        latest_session = await (await db.execute(
            """
            SELECT id, capabilities, telemetry
            FROM agent_sessions
            WHERE agent_id = ?
              AND runtime = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, runtime),
        )).fetchone()
        if latest_session:
            session_telemetry = _json_loads_or(latest_session["telemetry"], {})
            if registered_handle:
                session_telemetry["registeredHandle"] = registered_handle
            else:
                session_telemetry.pop("registeredHandle", None)
            session_capabilities = _session_capabilities_replacing_handle(latest_session["capabilities"], session_handle)
            await db.execute(
                """
                UPDATE agent_sessions
                SET session_handle = ?,
                    capabilities = ?,
                    telemetry = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    session_handle,
                    json.dumps(session_capabilities),
                    json.dumps(session_telemetry),
                    now,
                    latest_session["id"],
                ),
            )
        await db.commit()

        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": session_handle})
        return {
            "ok": True,
            "agentId": agent_id,
            "sessionHandle": session_handle,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/session/confirm")
async def confirm_agent_session(agent_id: str, req: AgentSessionResolveRequest, request: Request):
    """Sticky session identity (governance, 2026-05-30): operator confirms the
    NEW (pending) session id. Re-pins `session_handle := pending_session_id`,
    clears the pending id, and exits the `session-changed` state. Delivery now
    follows the new id. Idempotent: a 409 is returned if there is no pending id
    to confirm (nothing to resolve).
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        pending = str(row["pending_session_id"] or "").strip()
        if not pending:
            raise HTTPException(409, f"Agent '{agent_id}' has no pending session id to confirm")

        runtime = _normalize_runtime(row["runtime"] or "generic")
        session_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime_config = _json_loads_or(row["runtime_config"], {})
        # Re-pin: the new id becomes the live handle. Mirror the normal handle
        # write so runtime_state / capabilities stay consistent with the handle.
        runtime_state = _runtime_state_replacing_handle(runtime, row["runtime_state"], pending)
        capabilities = _default_capabilities_for(runtime, session_mode, pending, runtime_config)
        await db.execute(
            """
            UPDATE agents
            SET session_handle = ?,
                pending_session_id = '',
                runtime_state = ?,
                capabilities = ?,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                pending,
                json.dumps(runtime_state),
                json.dumps(capabilities),
                f"session-changed resolved: re-pinned to '{pending}' by {req.requestedBy or 'operator'}.",
                now,
                agent_id,
            ),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": pending})
        return {
            "ok": True,
            "agentId": agent_id,
            "resolution": "confirm",
            "sessionHandle": pending,
            "pendingSessionId": "",
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/session/keep")
async def keep_agent_session(agent_id: str, req: AgentSessionResolveRequest, request: Request):
    """Sticky session identity (governance, 2026-05-30): operator keeps the
    CURRENT (persisted) session id. Clears `pending_session_id`, leaves
    `session_handle` untouched, and surfaces the runtime's resume command so the
    operator can re-attach the agent to the persisted id (e.g. the agent drifted
    onto a fresh id and must be resumed back onto the pinned one). Idempotent:
    409 if there is no pending id to keep.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        pending = str(row["pending_session_id"] or "").strip()
        if not pending:
            raise HTTPException(409, f"Agent '{agent_id}' has no pending session id to keep")

        persisted_handle = str(row["session_handle"] or "").strip()
        runtime = _normalize_runtime(row["runtime"] or "generic")
        # Resume command for the operator to re-attach to the persisted id,
        # sourced from the runtime adapter (Python mirror of the JS contract).
        resume_command = ""
        try:
            from service.runtimes import adapter_for
            resume_command = adapter_for(runtime).resume_command(persisted_handle)
        except Exception:
            resume_command = ""
        await db.execute(
            """
            UPDATE agents
            SET pending_session_id = '',
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                (
                    f"session-changed resolved: keeping pinned id '{persisted_handle}' "
                    f"(resume: {resume_command}) by {req.requestedBy or 'operator'}."
                    if resume_command
                    else f"session-changed resolved: keeping pinned id '{persisted_handle}' by {req.requestedBy or 'operator'}."
                ),
                now,
                agent_id,
            ),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": persisted_handle})
        return {
            "ok": True,
            "agentId": agent_id,
            "resolution": "keep",
            "sessionHandle": persisted_handle,
            "pendingSessionId": "",
            "resumeCommand": resume_command,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/session-mode")
async def switch_agent_session_mode(agent_id: str, req: AgentSessionModeSwitchRequest, request: Request):
    """Plan 6 C1 (2026-05-26): operator-driven resident/managed mode flip.

    Today the wrapper auto-detects via `[ -t 0 ]`; this endpoint lets the
    operator override the agent's `session_mode` regardless of how the
    wrapper was launched. Edge cases the server protects against (unless
    `force=true` is passed):

    - Active dispatch run in flight -> 409 (switching mid-turn would
      stall the run; wait for it to finish).

    (The former hermes-without-gatewayUrl 409 guard was removed: under the
    api_server model resident hermes resumes its pinned session via
    `--resume` and never needs a gateway URL — it was a tui_gateway-era
    requirement.)

    Audit log: a `dispatch_events` row of type
    `mode_switch_<old>_to_<new>` is appended with body
    `agentId=<id> by=<requestedBy>`, providing traceability without a
    new table.

    State-transition side effects (C2):
    - resident -> managed: best-effort eager-spawn of a wrapper PTY so
      the next dispatch lands in a ready Console (mirrors the spawn
      path used by `_ensure_managed_pty_for_dispatch` during /dispatch).
    - managed -> resident: best-effort release of any active managed
      PTY by flipping its status to 'stopping' so the bridge reconciles
      the close cleanly. Operator must launch a resident `*-aify`
      session themselves for the agent to come back online.

    Side-effect failures do not roll back the mode change itself —
    operators can always re-attach manually. The `sideEffects` field in
    the response surfaces what happened (or what failed).
    """
    validate_name(agent_id, "agent ID")
    new_mode = _normalize_session_mode(req.mode)
    requested_raw = str(req.mode or "").strip().lower()
    if requested_raw not in _SESSION_MODES:
        raise HTTPException(400, "mode must be 'resident' or 'managed'")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        current_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime = _normalize_runtime(row["runtime"] or "generic")
        current_runtime_state = _json_loads_or(row["runtime_state"], {})
        resident_candidate = current_runtime_state.get("manualResidentCandidate")
        if not isinstance(resident_candidate, dict):
            resident_candidate = {}
        row_runtime_config = _json_loads_or(row["runtime_config"], {})
        candidate_runtime_config = resident_candidate.get("runtimeConfig") if isinstance(resident_candidate.get("runtimeConfig"), dict) else {}
        switch_runtime_config = (
            {**row_runtime_config, **candidate_runtime_config}
            if new_mode == "resident" and candidate_runtime_config
            else row_runtime_config
        )
        switch_session_handle = str(
            (resident_candidate.get("sessionHandle") if new_mode == "resident" else "")
            or row["session_handle"]
            or ""
        ).strip()
        # Adopt the resident candidate's runtime when switching to resident. A
        # resident wrapper of a different runtime (e.g. a hermes hermes-aify
        # session registering against an agent last seen as managed pi) records
        # itself as a manualResidentCandidate with runtime="hermes". Without
        # this, the switch promoted the candidate's bridge/handle/config but
        # kept the stale runtime, producing an inconsistent pi-resident agent
        # pointing at a hermes bridge — the switch appeared to do nothing.
        effective_runtime = runtime
        if new_mode == "resident":
            candidate_runtime = str(resident_candidate.get("runtime") or "").strip()
            if candidate_runtime:
                effective_runtime = _normalize_runtime(candidate_runtime)

        if current_mode == new_mode:
            return {
                "ok": True,
                "agentId": agent_id,
                "mode": new_mode,
                "previousMode": current_mode,
                "changed": False,
            }

        if not req.force:
            blocking = await _get_blocking_active_run(db, agent_id)
            if blocking:
                raise HTTPException(
                    409,
                    f"Agent has an active dispatch run (runId={blocking.get('runId')}); wait for it to finish or pass force=true",
                )
            # api_server model: resident hermes resumes its pinned session via --resume; no gatewayUrl needed (was a tui_gateway-era guard)
            if new_mode == "managed":
                managed_session = await (await db.execute(
                    """
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                      AND status NOT IN ('failed','lost','stopped','ended','completed','cancelled')
                    ORDER BY last_seen DESC
                    LIMIT 1
                    """,
                    (agent_id, runtime),
                )).fetchone()
                if not managed_session:
                    raise HTTPException(
                        409,
                        "Switch to managed requires an existing dashboard-managed session/backing. Spawn or recover the agent from an Environment, or pass force=true to only change metadata.",
                    )

        now = _now()
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        runtime_config = switch_runtime_config
        runtime_state = dict(current_runtime_state)
        runtime_state.pop("pendingResidentTakeover", None)
        if new_mode == "resident":
            if resident_candidate.get("bridgeId"):
                runtime_state["bridgeInstanceId"] = str(resident_candidate.get("bridgeId") or "")
            if resident_candidate:
                runtime_state["manualResidentCandidate"] = resident_candidate
        else:
            runtime_state.pop("manualResidentCandidate", None)
        runtime_state["ownership"] = {
            "mode": new_mode,
            "previousMode": current_mode,
            "reason": "manual_session_mode_switch",
            "requestedBy": requested_by,
            "at": now,
        }
        next_launch_mode = "managed" if new_mode == "managed" else "detached"
        capabilities = _default_capabilities_for(
            effective_runtime,
            new_mode,
            switch_session_handle,
            runtime_config,
        )
        next_machine_id = _normalize_machine_id(
            resident_candidate.get("machineId") or row["machine_id"] or ""
            if new_mode == "resident"
            else row["machine_id"] or ""
        )
        next_cwd = (
            str(resident_candidate.get("cwd") or row["cwd"] or "")
            if new_mode == "resident"
            else str(row["cwd"] or "")
        )
        await db.execute(
            """
            UPDATE agents
            SET session_mode = ?,
                runtime = ?,
                launch_mode = ?,
                session_handle = ?,
                machine_id = ?,
                cwd = ?,
                capabilities = ?,
                runtime_config = ?,
                runtime_state = ?,
                driver_state = 'idle',
                status = CASE WHEN status = 'stopped' THEN 'idle' ELSE status END,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                new_mode,
                effective_runtime,
                next_launch_mode,
                switch_session_handle,
                next_machine_id,
                next_cwd,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                json.dumps(runtime_state),
                f"Manually switched from {current_mode} to {new_mode} by {requested_by}"
                + (f" (runtime {runtime}->{effective_runtime})" if effective_runtime != runtime else "")
                + ".",
                now,
                agent_id,
            ),
        )
        # C1 audit log — `dispatch_events.run_id` is a NOT NULL FK to
        # `dispatch_runs(id)`, so we can't attach an agent-level event with
        # an empty run_id. Workaround: insert a synthetic anchor row into
        # `dispatch_runs` with status='completed' (so it never enters the
        # claim/queue paths) and a recognizable subject. Then attach the
        # mode_switch event to it. Operators see the audit row in the same
        # per-agent dispatch history view; no new table needed.
        event_type = f"mode_switch_{current_mode}_to_{new_mode}"
        audit_run_id = f"mode_switch_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO dispatch_runs (
                id, from_agent, target_agent, dispatch_mode, execution_mode,
                runtime, message_type, subject, body, status, summary, requested_at, finished_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                audit_run_id,
                requested_by,
                agent_id,
                "audit",
                "audit",
                effective_runtime,
                "audit",
                "session-mode-switch",
                f"agentId={agent_id} {current_mode}->{new_mode} by={requested_by}",
                "completed",
                event_type,
                now,
                now,
            ),
        )
        await _append_dispatch_event(
            db,
            audit_run_id,
            event_type,
            f"agentId={agent_id} by={requested_by}",
        )

        # C2 state-transition side effects. Wrapped in try/except so a side
        # effect failure (e.g., environment offline, no live agent_sessions
        # row) does NOT roll back the mode change — operators can still
        # re-spawn/attach manually. Failures surface in the response's
        # `sideEffects.error` field.
        settings = await _load_settings(db)
        side_effects: dict[str, Any] = {}
        try:
            if new_mode == "managed":
                # FIX SET B1 (2026-06-03): wrapper-backed managed runtimes
                # (codex/hermes) must NOT eager-start via
                # _ensure_managed_pty_for_dispatch — that re-attaches a PTY to the
                # leftover RESIDENT agent_sessions row (a resident `*-aify --resume`,
                # NOT a managed-warm worker), so no `managed-wrapper-child` bridge
                # registers and the next 'channel' run is rejected
                # `managed_wrapper_child_required` → queued forever (the lc-coder
                # resident→managed strand). Instead: RETIRE the leftover non-terminal
                # resident agent_sessions row(s) and cold-start a managed-warm
                # spawn_request so a bridge spawns a real managed worker whose
                # in-session MCP registers the wrapper-child claimer.
                if _managed_via_wrapper_for_runtime(settings, runtime):
                    await db.execute(
                        """
                        UPDATE agent_sessions
                        SET status = 'retired', last_seen = ?
                        WHERE agent_id = ?
                          AND COALESCE(status, '') NOT IN ('retired', 'stopped', 'terminated', 'failed')
                        """,
                        (now, agent_id),
                    )
                    coldstarted = await _coldstart_spawn_request_for_dispatch(
                        db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by
                    )
                    if coldstarted:
                        side_effects["managedSpawnRequested"] = True
                    else:
                        side_effects["error"] = (
                            "No online environment can host managed "
                            f"{runtime} for this agent; start an environment bridge that advertises {runtime}."
                        )
                else:
                    terminal = await _ensure_managed_pty_for_dispatch(
                        db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by
                    )
                    if terminal is not None:
                        # `_ensure_managed_pty_for_dispatch` returns either a sqlite
                        # Row (existing active terminal) or a dict (newly spawned).
                        try:
                            side_effects["managedTerminalId"] = terminal["id"] if "id" in terminal.keys() else terminal.get("id")
                        except Exception:
                            side_effects["managedTerminalId"] = None
                    else:
                        side_effects["error"] = "No managed session/backing was available for eager PTY start."
            else:
                # managed -> resident: best-effort stop of any active managed PTY.
                active = await _active_terminal_for_agent(db, agent_id, settings=settings)
                if active is not None:
                    terminal_id = active["terminal_id"] if "terminal_id" in active.keys() else None
                    session_id = active["session_id"] if "session_id" in active.keys() else ""
                    if terminal_id:
                        await db.execute(
                            "UPDATE terminal_sessions SET status = 'stopping', updated_at = ? WHERE id = ?",
                            (now, terminal_id),
                        )
                        if session_id:
                            await db.execute(
                                "UPDATE agent_sessions SET terminal_status = 'stopping', last_seen = ? WHERE id = ?",
                                (now, session_id),
                            )
                        side_effects["stoppedTerminalId"] = terminal_id
        except Exception as exc:  # pragma: no cover — surface, do not abort
            logger.warning("session-mode side-effect failed for %s: %s", agent_id, exc)
            side_effects["error"] = str(exc)

        await db.commit()
        # Takeover/resume command for the operator. On a managed -> resident
        # switch this is the command the operator runs to drive the SAME session
        # interactively; mirrored in the dashboard. Best-effort (empty if the
        # adapter has none).
        resume_command = _resume_command_for(effective_runtime, switch_session_handle, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_session_mode_updated",
                {"agentId": agent_id, "mode": new_mode, "previousMode": current_mode},
            )
        return {
            "ok": True,
            "agentId": agent_id,
            "mode": new_mode,
            "previousMode": current_mode,
            "changed": True,
            "resumeCommand": resume_command,
            "sideEffects": side_effects,
        }
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/ready")
async def update_agent_ready(agent_id: str, req: AgentReadyUpdate, request: Request):
    """Plan 4 task 12 (2026-05-25): bridge POSTs here when an adapter
    controller's start() has completed initial handshake. This stores an
    internal readiness bit; public idle-live status remains `online`.

    Upsert preserves any existing turn_busy/turn_run_id state — clearing
    ready does NOT also clear turn_busy and vice versa.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        now = _now()
        ready_int = 1 if req.ready else 0
        # Upsert agent_turn_state: insert with ready, or update only ready
        # (and updated_at) on conflict — turn_busy and run/bridge/runtime
        # fields are owned by the dispatch path, not by this endpoint.
        await db.execute(
            """
            INSERT INTO agent_turn_state
                (agent_id, turn_busy, turn_run_id, turn_bridge_id,
                 turn_runtime, turn_updated_at, ready)
            VALUES (?, 0, '', '', '', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                ready = excluded.ready,
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, now, ready_int),
        )
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_ready",
                {"agentId": agent_id, "ready": bool(req.ready)},
            )
        return {"ok": True, "agentId": agent_id, "ready": bool(req.ready)}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/runtime-state")
async def update_agent_runtime_state(agent_id: str, req: AgentRuntimeStateUpdate, request: Request):
    db = await get_db()
    try:
        now = _now()
        current = await (await db.execute("SELECT runtime, session_mode, session_handle, capabilities, runtime_config, runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not current:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        next_state = dict(req.runtimeState or {})
        current_state = _json_loads_or(current["runtime_state"], {})
        # Preserve service-managed runtime_state keys that the bridge
        # doesn't know about (or won't repopulate on every PATCH).
        # Without this, a bridge PATCH from the dispatch path (which
        # only carries sessionId/sessionFile etc.) silently clobbers
        # virtualTerminalId set earlier by /virtual-terminal/ensure —
        # the dashboard Console-reattach then looks up a stale pointer.
        # Bridges that genuinely need to clear these should send
        # explicit null (handled below).
        SERVICE_MANAGED_RUNTIME_STATE_KEYS = ("virtualTerminal", "virtualTerminalId", "manualResidentCandidate")
        for key in SERVICE_MANAGED_RUNTIME_STATE_KEYS:
            if key not in next_state and key in current_state:
                next_state[key] = current_state[key]
            elif next_state.get(key) is None and key in next_state:
                # Caller explicitly passed null → honor the clear.
                next_state.pop(key, None)
        if _normalize_session_mode(current["session_mode"] or "resident") == "managed":
            current_bridge = str(current_state.get("bridgeInstanceId") or "").strip()
            next_bridge = str(next_state.get("bridgeInstanceId") or "").strip()
            if current_bridge and next_bridge and current_bridge != next_bridge:
                next_state["bridgeInstanceId"] = current_bridge
                if current_state.get("environmentId"):
                    next_state["environmentId"] = current_state.get("environmentId")
        # Automatic resident takeover is disabled. A resident bridge heartbeat
        # must not stash or preserve pending takeover state; operators flip
        # ownership explicitly with PATCH /agents/{id}/session-mode.
        next_state.pop("pendingResidentTakeover", None)
        reported_handle = _runtime_handle_from_state(current["runtime"], next_state)
        if reported_handle:
            capabilities = _default_capabilities_for(
                current["runtime"],
                current["session_mode"] or "resident",
                reported_handle,
                _json_loads_or(current["runtime_config"], {}),
            )
            await db.execute(
                "UPDATE agents SET runtime_state = ?, session_handle = ?, capabilities = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_state), reported_handle, json.dumps(capabilities), now, agent_id)
            )
        else:
            await db.execute(
                "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_state), now, agent_id)
            )
        await _touch_current_agent_session(db, agent_id, next_state, now)
        await db.commit()
        return {"ok": True, "agentId": agent_id, "runtimeState": next_state}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/resident-lost")
async def resident_lost(agent_id: str, req: AgentResidentLostRequest, request: Request):
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        runtime_state = _json_loads_or(row["runtime_state"], {})
        current_bridge_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
        bridge_id = str(req.bridgeId or "").strip()
        if bridge_id and current_bridge_id and bridge_id != current_bridge_id:
            return {
                "ok": True,
                "ignored": True,
                "reason": "bridge_not_current",
                "agentId": agent_id,
                "currentBridgeId": current_bridge_id,
                "bridgeId": bridge_id,
            }

        if bridge_id:
            await db.execute(
                """
                UPDATE bridge_instances
                SET superseded_by = CASE WHEN COALESCE(superseded_by, '') = '' THEN 'resident-lost' ELSE superseded_by END,
                    superseded_at = COALESCE(superseded_at, ?)
                WHERE id = ? AND agent_id = ?
                """,
                (now, bridge_id, agent_id),
            )

        settings = await _load_settings(db)
        returned, transition = await _auto_return_resident_to_managed_if_possible(
            db,
            row,
            settings=settings,
            force=True,
            reason="resident_runtime_lost",
        )

        if not transition:
            await db.execute(
                """
                UPDATE agents
                SET status = 'stopped',
                    status_note = ?,
                    launch_mode = 'none',
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    str(req.reason or "Resident runtime bridge was lost and no managed backing was available.")[:500],
                    now,
                    agent_id,
                ),
            )
            returned = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
            transition = "resident_to_stopped"

        await db.commit()
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        status = await _compute_agent_status(returned, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_resident_lost", {"agentId": agent_id, "transition": transition})
        return {
            "ok": True,
            "agentId": agent_id,
            "transition": transition,
            "agent": _agent_record_to_dict(returned, status, 0, dispatch_state),
        }
    finally:
        await db.close()


async def _touch_current_agent_session(db, agent_id: str, runtime_state: dict[str, Any] | None, now: str) -> None:
    """Keep the dashboard backing record fresh when a managed runtime is used."""
    state = runtime_state or {}
    spawn_request_id = str(state.get("spawnRequestId") or "").strip()
    environment_id = str(state.get("environmentId") or "").strip()
    runtime_handle = str(state.get("sessionId") or state.get("threadId") or state.get("sessionFile") or "").strip()
    if spawn_request_id:
        await db.execute(
            """
            UPDATE agent_sessions
            SET last_seen = ?,
                session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                status = CASE
                    WHEN status IN ('starting', 'recovering', 'restarting') THEN 'running'
                    ELSE status
                END
            WHERE agent_id = ?
              AND spawn_request_id = ?
              AND status NOT IN ('failed', 'lost', 'stopped', 'ended', 'completed', 'cancelled')
            """,
            (now, runtime_handle, runtime_handle, agent_id, spawn_request_id),
        )
        return
    if environment_id:
        await db.execute(
            """
            UPDATE agent_sessions
            SET last_seen = ?,
                session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                status = CASE
                    WHEN status IN ('starting', 'recovering', 'restarting') THEN 'running'
                    ELSE status
                END
            WHERE id = (
                SELECT id
                FROM agent_sessions
                WHERE agent_id = ?
                  AND environment_id = ?
                  AND status NOT IN ('failed', 'lost', 'stopped', 'ended', 'completed', 'cancelled')
                ORDER BY last_seen DESC
                LIMIT 1
            )
            """,
            (now, runtime_handle, runtime_handle, agent_id, environment_id),
        )


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

    key_material = session_handle or str(config.get("gatewayUrl") or "") or bridge_id or agent_id
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


# ─── Messages ────────────────────────────────────────────────────────────────

@router.post("/messages/send")
async def send_message(req: MessageSend, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        # NOTE: do NOT clear turn_busy here based on the agent sending a
        # message. The agent might send a reply and then keep working
        # (more tool calls, more analysis, more messages) — clearing on
        # response would flip status to "active" while real work is
        # still happening. Turn-end is a harness-level signal: each
        # runtime delivers its own (codex turn/completed, pi agent_end,
        # hermes process exit, opencode SDK turn-complete). Resident
        # claude under claude-channel.js needs its Stop hook to call
        # the bridge — see install.sh's claude wrapper installation.
        msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; message was sent unthreaded.'
            )

        # ASYMMETRY: replies bypass the live-wake hard-gate by design.
        # A reply must ALWAYS be persisted + threaded (and close its
        # require_reply run) even when the recipient can't be live-woken —
        # the recipient simply sees it in their inbox. Hard-rejecting a
        # reply because the recipient's bridge is stale dropped legitimate
        # replies (broke managed-hermes self-reply when the original
        # sender's resident bridge was stale) and left the require_reply
        # run open forever. The live-wake hard-gate below stays in force
        # only for NEW dispatches (requests/etc.), never for replies.
        # A reply is identified by a resolved inReplyTo OR type=="response".
        is_reply = bool(resolved_in_reply_to) or str(req.type or "").strip().lower() == "response"

        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": []}

        launchable_recipients = []
        not_started = []
        console_recipients = {}
        dispatch_recipients = [r for r in recipients if r != "dashboard"]
        if req.trigger:
            prefer_steer = (req.steer is not False) and not bool(req.queueIfBusy)
            allow_queue_busy = bool(req.queueIfBusy) or prefer_steer or str(req.type or "").strip().lower() == "response"
            launchable_recipients, not_started = await _preflight_live_send_recipients(
                db,
                dispatch_recipients,
                allow_steer=prefer_steer,
                allow_queue_busy=allow_queue_busy,
            )
            # ASYMMETRY: do NOT hard-reject a reply here. Replies fall through
            # to persist + thread regardless of recipient live-startability
            # (see is_reply note above). Only NEW dispatches hard-gate.
            if not_started and not is_reply:
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "consoleDeliveries": [],
                    "warnings": warnings,
                }
            settings = await _load_settings(db)
            channel_backing_failed = set()
            for recipient_id, _execution_mode in launchable_recipients:
                row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))).fetchone()
                if row:
                    row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
                if not row:
                    continue
                runtime = _normalize_runtime(row["runtime"] or "generic")
                # Queue only waits behind real active/queued work. For an idle
                # terminal-backed target, it should still use the normal live
                # delivery path instead of creating an orphan dispatch queue.
                if bool(req.queueIfBusy):
                    dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
                    # Three signals of "currently busy":
                    # 1. hasActiveRun: tracked dispatch_run in claimed/running
                    # 2. queuedRuns > 0: prior queue already pending
                    # 3. turn_busy=1 (fresh): the agent is mid-turn even if
                    #    no tracked dispatch_run is in flight. Operator-
                    #    reported 2026-05-22: queue button sent immediately
                    #    because require_reply=0 info messages auto-complete
                    #    their dispatch_run on delivery → hasActiveRun goes
                    #    false → queue fires the next message immediately
                    #    while the assistant is still working. turn_busy
                    #    is the harness-level signal that survives the
                    #    auto-completion.
                    is_turn_busy = False
                    try:
                        tb_row = await (await db.execute(
                            "SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
                            (recipient_id,),
                        )).fetchone()
                        if tb_row and int(tb_row["turn_busy"] or 0) == 1:
                            tb_epoch = _iso_to_epoch(str(tb_row["turn_updated_at"] or ""))
                            if tb_epoch and (datetime.now(timezone.utc).timestamp() - tb_epoch) <= TURN_BUSY_STALE_SECONDS:
                                is_turn_busy = True
                    except Exception:
                        is_turn_busy = False
                    if (
                        dispatch_state.get("hasActiveRun")
                        or int(dispatch_state.get("queuedRuns") or 0) > 0
                        or is_turn_busy
                    ):
                        continue
                execution_mode = str(_execution_mode or "").strip().lower()
                # Native-managed runtimes (codex/pi/opencode/hermes) — only
                # route through PTY-input when the operator opted into
                # the legacy via-console delivery mode AND managed-
                # terminal-backing is enabled. Default
                # (insert_messages_via_console=false) falls through and
                # the dispatch is claimed by the runtime's native RPC
                # adapter (createCodexController, createPiController,
                # opencode SDK) on its /dispatch/claim poll.
                if runtime in _NATIVE_MANAGED_RUNTIMES:
                    # Wrapper-backed managed (operator-stated 2026-05-25): if
                    # the runtime is in managed_via_wrapper, the wrapper PTY
                    # MUST exist to claim — auto-spawn here so an available
                    # agent gets its console started on first message arrival
                    # (mirror of the operator's "send → console auto-starts
                    # → status flips" model).
                    if (
                        execution_mode == "channel"
                        and _managed_terminal_backing_enabled(settings)
                        and _managed_via_wrapper_for_runtime(settings, runtime)
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        # FIX SET B2 (2026-06-03): for a wrapper-backed runtime a
                        # leftover RESIDENT-mode terminal_session must NOT short-
                        # circuit the managed coldstart. _active_terminal_for_agent /
                        # _ensure_managed_pty_for_dispatch would re-attach a PTY to
                        # that stale resident row (a resident `--resume`, NOT a
                        # managed-warm worker), so no `managed-wrapper-child` bridge
                        # ever registers and the 'channel' run is rejected
                        # `managed_wrapper_child_required` → queued forever (the
                        # lc-coder strand). Only a LIVE managed-wrapper-child proves a
                        # managed worker is actually backing this agent; absent it,
                        # drop the leftover terminal so the coldstart branch below
                        # fires and a managed-warm worker is spawned.
                        if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                            console_terminal = None
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                            # The PTY re-attach above can still resolve a leftover
                            # resident row; re-gate on the live wrapper-child so a
                            # non-managed terminal never suppresses the coldstart.
                            if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                                console_terminal = None
                        if not console_terminal:
                            # Phase 2 lazy-autostart: no live wrapper PTY to
                            # back this agent (it was only registered, never
                            # run — the operator's `available` sc-coder case).
                            # Instead of rejecting, cold-start a spawn_request
                            # (auto-binding an online env when none is bound)
                            # so a bridge spawns the wrapper and claims this
                            # dispatch on its next poll. Only reject when no
                            # online environment can host the runtime.
                            coldstarted = await _coldstart_spawn_request_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                            if not coldstarted and not await _has_claimable_spawn_request(db, recipient_id):
                                not_started.append(
                                    _dispatch_fix_hint(
                                        recipient_id,
                                        row,
                                        f"No online environment can host managed {runtime} for this agent; start an environment bridge that advertises {runtime}, or recover the session.",
                                    )
                                )
                                channel_backing_failed.add(recipient_id)
                        # Do NOT add to console_recipients (that's the legacy
                        # PTY-input delivery path). Wrapper child bridge claims
                        # via /dispatch/claim once its in-process MCP boots.
                        # Just let the dispatch sit queued; it'll get picked up
                        # within a polling cycle (3s) once the wrapper is up.
                        continue
                    if (
                        execution_mode == "managed"
                        and _managed_terminal_backing_enabled(settings)
                        and _insert_messages_via_console(settings)
                        and runtime not in {"pi", "opencode"}
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if console_terminal:
                            console_recipients[recipient_id] = console_terminal
                            continue
                    continue
                # Managed Claude PTY-input branch — only fires when the
                # operator has opted into the legacy via-console delivery
                # mode (insert_messages_via_console=true). Default-false
                # routing flows through the channel branch below: the run
                # is left launchable with execution_mode='channel' (see
                # _apply_channel_routing_to_claude_runs after
                # _create_dispatch_runs) so claude-channel.js inside the
                # wrapper-hosted claude-aify claims it and emits the
                # message as a channel wake-up event.
                if (
                    runtime in _CHANNEL_MANAGED_RUNTIMES
                    and _execution_mode == "channel"
                    and _insert_messages_via_console(settings)
                ):
                    console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                    if not console_terminal:
                        console_terminal = await _ensure_managed_pty_for_dispatch(
                            db,
                            recipient_id,
                            runtime=runtime,
                            settings=settings,
                            requested_by=req.from_agent,
                        )
                    if console_terminal:
                        console_recipients[recipient_id] = console_terminal
                    else:
                        not_started.append(
                            _dispatch_fix_hint(
                                recipient_id,
                                row,
                                "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session.",
                            )
                        )
                        channel_backing_failed.add(recipient_id)
                    continue
                if runtime in _CHANNEL_MANAGED_RUNTIMES:
                    # Channel-mode managed Claude (insert_messages_via_console=false)
                    # needs a wrapper PTY running so claude-aify's
                    # claude-channel.js child actually polls
                    # /dispatch/claim for this agent and picks up the
                    # channel-routed dispatch. Without it, the run sits
                    # queued forever (originally observed in
                    # run_1779309370301). We don't inject input — the
                    # PTY is the host for the subscriber, not the
                    # delivery channel. Existing terminal is reused
                    # (slice-3 reuse semantics); only spawned if absent.
                    if (
                        not _insert_messages_via_console(settings)
                        and _managed_terminal_backing_enabled(settings)
                        and _execution_mode == "channel"
                    ):
                        existing = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not existing:
                            try:
                                await _ensure_managed_pty_for_dispatch(
                                    db,
                                    recipient_id,
                                    runtime=runtime,
                                    settings=settings,
                                    requested_by=req.from_agent,
                                )
                            except Exception:
                                # Best-effort. claude-channel.js may still
                                # pick this up if a wrapper exists in
                                # another env; otherwise dispatch stays
                                # queued and operator can spawn manually.
                                pass
                    continue
                console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                if not console_terminal:
                    console_terminal = await _ensure_managed_pty_for_dispatch(
                        db,
                        recipient_id,
                        runtime=runtime,
                        settings=settings,
                        requested_by=req.from_agent,
                    )
                if console_terminal:
                    console_recipients[recipient_id] = console_terminal
            launchable_recipients = [
                (recipient_id, execution_mode)
                for recipient_id, execution_mode in launchable_recipients
                if recipient_id not in console_recipients and recipient_id not in channel_backing_failed
            ]
            # ASYMMETRY: replies are never hard-rejected — see is_reply note
            # above. Fall through to persist + thread the reply.
            if not_started and not is_reply:
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "consoleDeliveries": [],
                    "warnings": warnings,
                }

        linked_result_message_id = _primary_result_message_id(msg_id, recipients)

        for r in recipients:
            recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
            dispatch_requested = 1 if req.trigger and r != "dashboard" else 0
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (recipient_message_id,
                 req.from_agent, r, "direct", req.type, req.subject, req.body, req.priority, dispatch_requested, resolved_in_reply_to, ts)
            )

        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=linked_result_message_id,
                reply_type=req.type,
                reply_body=req.body,
            )
        else:
            for r in recipients:
                recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
                await _link_unthreaded_reply_to_recent_dispatch_run(
                    db,
                    from_agent=req.from_agent,
                    to_agent=r,
                    reply_message_id=recipient_message_id,
                    reply_type=req.type,
                    reply_subject=req.subject,
                    reply_body=req.body,
                    reply_timestamp_ms=ts,
                )

        dispatch_runs = []
        if req.trigger:
            require_reply = _dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            dispatch_runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode="start_if_possible",
                execution_mode="managed",
                requested_runtime=None,
                message_id=msg_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=prefer_steer,
                require_reply=require_reply,
            )
            dispatch_runs = await _finalize_dispatch_runs(db, dispatch_runs, launchable_recipients, not_started)
            await _apply_channel_routing_to_claude_runs(db, dispatch_runs, settings)

        console_deliveries = []
        if req.trigger:
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            for recipient_id, terminal in console_recipients.items():
                terminal_id = str(terminal["terminal_id"] or "").strip()
                recipient_message_id = source_message_ids.get(recipient_id, msg_id)
                terminal_runtime = _normalize_runtime(terminal["runtime"] or "")
                control_id = await _append_terminal_control(
                    db,
                    terminal_id=terminal_id,
                    environment_id=terminal["environment_id"],
                    bridge_id=terminal["bridge_id"] or "",
                    action="input",
                    requested_by=req.from_agent,
                    body=_console_dispatch_input_body(
                        req,
                        recipient_id=recipient_id,
                        message_id=recipient_message_id,
                        bracketed_paste=True,
                    ),
                )
                submit_control_id = ""
                await _append_terminal_event(
                    db,
                    terminal_id,
                    "terminal_input_requested",
                    json.dumps({
                        "requestedBy": req.from_agent,
                        "controlId": control_id,
                        "submitControlId": submit_control_id,
                        "source": "message_send",
                        "messageId": recipient_message_id,
                    }),
                )
                contract_run_id = await _record_terminal_delivery_contract(
                    db,
                    source_message_id=recipient_message_id,
                    from_agent=req.from_agent,
                    recipient_id=recipient_id,
                    message_type=req.type,
                    subject=req.subject,
                    body=req.body,
                    priority=req.priority,
                    in_reply_to=resolved_in_reply_to,
                    require_reply=_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type)),
                    terminal_id=terminal_id,
                    control_id=control_id,
                    runtime=terminal["runtime"] or "",
                )
                console_deliveries.append({
                    "targetAgentId": recipient_id,
                    "terminalId": terminal_id,
                    "controlId": control_id,
                    "contractRunId": contract_run_id,
                    "status": "sent_to_console",
                })

        # Gather recipient status info for sender context
        recipient_info = {}
        for r in recipients:
            info = await _get_recipient_info(db, r)
            if info:
                recipient_info[r] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_sent", {"id": msg_id, "from": req.from_agent, "to": recipients, "subject": req.subject})
            for r in recipients:
                await ws.notify_agent(r, "new_message", {"from": req.from_agent, "subject": req.subject})
            for run in dispatch_runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
            for delivery in console_deliveries:
                await ws.broadcast("terminal_control_requested", {"terminalId": delivery["terminalId"], "action": "input"})
        # Wake up any listening agents
        for r in recipients:
            _wake_agent(r)
        return {
            "ok": True,
            "messageId": msg_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "dispatchRuns": dispatch_runs,
            "notStarted": not_started,
            "consoleDeliveries": console_deliveries,
            "warnings": warnings,
        }
    finally:
        await db.close()


@router.get("/messages/inbox/{agent_id}")
async def get_inbox(
    agent_id: str, request: Request,
    filter: str = Query("unread", pattern="^(unread|read|all)$"),
    fromAgent: Optional[str] = None, fromRole: Optional[str] = None,
    type: Optional[str] = None, limit: int = Query(200, ge=1, le=1000),
    mode: str = Query("full", pattern="^(full|headers)$"),
    messageId: Optional[str] = None,
    peek: Optional[str] = None,
):
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        include_body = mode != "headers"
        if messageId:
            base = """SELECT m.*, r.read_at FROM messages m
                      LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                      WHERE m.to_agent = ? AND m.id = ?"""
            params = [agent_id, agent_id, messageId]
        else:
            # Build query
            if filter == "unread":
                base = """SELECT m.*, NULL as read_at FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ? AND r.message_id IS NULL"""
                params = [agent_id, agent_id]
            elif filter == "read":
                base = """SELECT m.*, r.read_at FROM messages m
                          JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]
            else:
                base = """SELECT m.*, r.read_at FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]

        if fromAgent:
            base += " AND m.from_agent = ?"
            params.append(fromAgent)
        if fromRole:
            base += " AND m.from_agent IN (SELECT id FROM agents WHERE role = ?)"
            params.append(fromRole)
        if type:
            base += " AND m.type = ?"
            params.append(type)

        base += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(1 if messageId else limit)

        cursor = await db.execute(base, params)
        rows = await cursor.fetchall()

        # Count total (without limit)
        count_q = base.replace("SELECT m.*, NULL as read_at", "SELECT COUNT(*)").replace("SELECT m.*, r.read_at", "SELECT COUNT(*)")
        count_q = count_q[:count_q.rfind("LIMIT")]
        c = await db.execute(count_q, params[:-1])
        total = (await c.fetchone())[0]

        messages = []
        for row in rows:
            msg = _serialize_inbox_message(row, include_body=include_body)
            # Include parent message context for replies
            if row["in_reply_to"]:
                pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                parent = await pc.fetchone()
                if parent:
                    msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
            messages.append(msg)

        # Mark as read + update status (unless peek)
        if not peek:
            now = _now()
            unread_found = 0
            for msg in messages:
                if not msg["read"]:
                    unread_found += 1
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        (msg["id"], agent_id, now)
                    )
            # Complete stuck dispatch runs linked to messages we just read.
            # Only claimed/running (stuck from dead bridges) — NOT queued.
            # Queued dispatches should be left for the bridge to claim and
            # execute as a turn. Completing them here would prevent the wake.
            if unread_found > 0:
                read_msg_ids = [msg["id"] for msg in messages if not msg["read"]]
                for msg_id in read_msg_ids:
                    await db.execute(
                        """
                        UPDATE dispatch_runs
                        SET status = 'completed', summary = 'Message read via inbox', finished_at = ?
                        WHERE message_id = ? AND target_agent = ? AND status IN ('claimed', 'running')
                        """,
                        (now, msg_id, agent_id),
                    )

            # Smart status: got messages = working, no messages = idle
            new_status = "working" if unread_found > 0 else "idle"
            await db.execute(
                "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE ? END WHERE id = ?",
                (now, new_status, agent_id)
            )
            await db.commit()

        return {"total": total, "showing": len(messages), "messages": messages}
    finally:
        await db.close()


@router.get("/messages/recent")
async def recent_messages(
    request: Request,
    limit: int = Query(80, ge=1, le=250),
):
    """Recent human-scale message activity without channel fanout duplicates."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT *
            FROM messages
            WHERE
              (source = 'direct' AND to_agent IS NOT NULL)
              OR (source = 'channel' AND to_agent IS NULL)
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        messages = []
        for row in await cursor.fetchall():
            messages.append({
                "id": row["id"],
                "from": row["from_agent"],
                "to": row["to_agent"],
                "channel": row["channel"],
                "source": row["source"],
                "type": row["type"],
                "subject": row["subject"],
                "preview": _clip_text(row["body"] or "", 240),
                "priority": row["priority"],
                "timestamp": row["timestamp"],
                "inReplyTo": row["in_reply_to"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
            })
        return {"ok": True, "messages": messages, "total": len(messages)}
    finally:
        await db.close()


@router.get("/messages/search")
async def search_messages(
    request: Request, query: str = "",
    agentId: Optional[str] = None,
    scope: str = Query("all", pattern="^(inbox|shared|all)$"),
    limit: int = Query(10, ge=1, le=100),
):
    db = await get_db()
    try:
        q = f"%{query.lower()}%"
        results = []

        if agentId and scope in ("inbox", "all"):
            cursor = await db.execute(
                "SELECT * FROM messages WHERE to_agent = ? AND (LOWER(subject) LIKE ? OR LOWER(body) LIKE ? OR LOWER(from_agent) LIKE ?) ORDER BY timestamp DESC LIMIT ?",
                (agentId, q, q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "message", "id": row["id"], "from": row["from_agent"],
                    "subject": row["subject"], "preview": (row["body"] or "")[:150],
                })

        if scope in ("shared", "all"):
            cursor = await db.execute(
                "SELECT * FROM shared_artifacts WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT ?",
                (q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "shared", "name": row["name"], "from": row["from_agent"],
                    "description": row["description"], "size": row["size"],
                })

        return {"results": results[:limit], "total": len(results)}
    finally:
        await db.close()


# ─── Agent Info ──────────────────────────────────────────────────────────────

@router.get("/agents/{agent_id}/last-read")
async def agent_last_read(agent_id: str, request: Request):
    """Get the last message this agent read — useful for checking if they've seen your message."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT m.*, r.read_at FROM read_receipts r JOIN messages m ON m.id = r.message_id WHERE r.agent_id = ? ORDER BY r.read_at DESC LIMIT 1",
            (agent_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return {"agentId": agent_id, "lastRead": None}
        return {"agentId": agent_id, "lastRead": {
            "messageId": row["id"], "from": row["from_agent"], "subject": row["subject"],
            "type": row["type"], "readAt": row["read_at"], "timestamp": row["timestamp"],
        }}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str, request: Request):
    """Lightweight heartbeat — bridge poll loop calls this to signal liveness."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    bridge_id = str(body.get("bridgeId", "") or "").strip()
    terminal_id = str(body.get("terminalId", "") or "").strip()
    bridge_kind = str(body.get("bridgeKind", "") or "").strip().lower()
    now = _now()
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        # Mode FSM release signal (Task 4.1, 2026-05-30). Symmetric with the
        # claim path: a DISPLACED managed sidecar (bridgeKind="channel-sidecar")
        # pulsing turn_busy via heartbeat is told to RELEASE once the agent has
        # been switched to resident, so it stops driving even between claims.
        # driver_state guard (2026-05-31, sc-manager): see the claim-path comment.
        # A live resident driver (driver_state='driving') keeps its own delivery
        # sidecar; only a displaced managed driver (not 'driving') is released.
        if bridge_kind == "channel-sidecar":
            mode_row = await (await db.execute(
                "SELECT session_mode, driver_state FROM agents WHERE id = ?",
                (agent_id,),
            )).fetchone()
            if (
                mode_row
                and _normalize_session_mode(mode_row["session_mode"] or "resident") != "managed"
                and str((mode_row["driver_state"] if "driver_state" in mode_row.keys() else "") or "").strip().lower() != "driving"
            ):
                return {"ok": True, "release": True}
        if bridge_id:
            bridge_row = await (await db.execute(
                "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
                (bridge_id, agent_id),
            )).fetchone()
            if bridge_row and str(bridge_row["superseded_by"] or "").strip():
                return {
                    "ok": False,
                    "ignored": True,
                    "reason": "bridge_superseded",
                    "supersededBy": str(bridge_row["superseded_by"] or "").strip(),
                }
        await db.execute(
            "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?",
            (now, agent_id),
        )
        if bridge_id:
            if terminal_id:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ?, terminal_id = ? WHERE id = ? AND agent_id = ?",
                    (now, terminal_id, bridge_id, agent_id),
                )
            else:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                    (now, bridge_id, agent_id),
                )
        # Unconditional liveness beat (Workstream A, 2026-06-01). A long-lived
        # bridge posts {bridgeId, bridgeKind, liveness:true} on a fixed interval
        # regardless of turn activity, so last_seen is a true "alive now" signal.
        # Unlike the plain UPDATE above (which no-ops when the bridge has no row
        # yet — e.g. an idle channel-sidecar that never claimed), this UPSERTS the
        # row, touching ONLY last_seen + bridge_kind. It never clears
        # superseded_by and never touches turn state. (A superseded existing row
        # is already short-circuited by the guard above.)
        if body.get("liveness") and bridge_id:
            arow = await (await db.execute(
                "SELECT machine_id, runtime FROM agents WHERE id = ?", (agent_id,),
            )).fetchone()
            arow_machine = (arow["machine_id"] if arow else "") or ""
            arow_runtime = (arow["runtime"] if arow else "") or "generic"
            if bridge_kind == "channel-sidecar":
                await _record_channel_sidecar_heartbeat(
                    db,
                    bridge_id=bridge_id,
                    agent_id=agent_id,
                    machine_id=arow_machine,
                    runtime=arow_runtime,
                    now=now,
                )
            else:
                # FIX SET B3 (2026-06-03): the 30s liveness beat from the host-side
                # bridge (server.js) posts bridgeKind="resident", but the SAME agent
                # may have a wrapper-child / channel-sidecar bridge row that registered
                # the authoritative managed kind. A plain COALESCE(NULLIF(?,''),...)
                # let that generic "resident" beat DEMOTE a 'managed-wrapper-child'
                # (or 'channel-sidecar') back to 'resident' — after which
                # _has_live_managed_wrapper_child / _has_live_channel_sidecar stop
                # matching and the managed agent loses its claimer (the lc-coder /
                # codex-managed strand). Guard: an incoming '' or 'resident' can NEVER
                # overwrite an existing 'managed-wrapper-child' or 'channel-sidecar';
                # any other incoming kind still COALESCE-wins as before.
                updated = await db.execute(
                    "UPDATE bridge_instances SET last_seen = ?, "
                    "bridge_kind = CASE "
                    "WHEN COALESCE(bridge_kind, '') IN ('managed-wrapper-child', 'channel-sidecar') "
                    "AND COALESCE(?, '') IN ('', 'resident') THEN bridge_kind "
                    "ELSE COALESCE(NULLIF(?, ''), bridge_kind) END "
                    "WHERE id = ? AND agent_id = ?",
                    (now, bridge_kind, bridge_kind, bridge_id, agent_id),
                )
                if not getattr(updated, "rowcount", 0):
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO bridge_instances (
                            id, agent_id, machine_id, runtime, session_mode,
                            session_handle, terminal_id, bridge_kind,
                            registered_at, last_seen, superseded_by, superseded_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (bridge_id, agent_id,
                         _normalize_machine_id(arow_machine),
                         arow_runtime,
                         "managed", "", "", bridge_kind or "resident",
                         now, now, "", None),
                    )
                    await db.execute(
                        "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                        (now, bridge_id, agent_id),
                    )
        # Authoritative turn-busy signal (contract with the bridge). Missing
        # "turnBusy" → liveness only (old-bridge safe). turnBusy=true: latest
        # bridge wins. turnBusy=false: only the owning bridge+run may clear,
        # so a stale false from a superseded bridge/run cannot wipe a newer
        # active turn.
        if "turnBusy" in body:
            turn_busy = bool(body.get("turnBusy"))
            turn_run_id = str(body.get("turnRunId", "") or "").strip()
            turn_runtime = str(body.get("turnRuntime", "") or "").strip()
            if turn_busy:
                await db.execute(
                    """
                    INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        turn_busy = 1,
                        turn_run_id = excluded.turn_run_id,
                        turn_bridge_id = excluded.turn_bridge_id,
                        turn_runtime = excluded.turn_runtime,
                        turn_updated_at = excluded.turn_updated_at
                    """,
                    (agent_id, turn_run_id, bridge_id, turn_runtime, now),
                )
            else:
                cur = await (await db.execute(
                    "SELECT turn_bridge_id, turn_run_id FROM agent_turn_state WHERE agent_id = ?",
                    (agent_id,),
                )).fetchone()
                if cur:
                    stored_bridge = str(cur["turn_bridge_id"] or "").strip()
                    stored_run = str(cur["turn_run_id"] or "").strip()
                    if stored_bridge == bridge_id and (not stored_run or stored_run == turn_run_id):
                        await db.execute(
                            "UPDATE agent_turn_state SET turn_busy = 0, turn_updated_at = ? WHERE agent_id = ?",
                            (now, agent_id),
                        )
            # A turn_busy flip changes derived status (working ⇄ idle). Invalidate
            # the live-state cache so the next read recomputes immediately, instead
            # of lagging up to the 60s reconcile sweep. Symmetric with the dedicated
            # /turn-start and /turn-end endpoints, which already invalidate.
            await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/claimer-lease")
async def post_claimer_lease(agent_id: str, request: Request):
    """WS5 Task 5.1 (2026-06-02): record a delivery-loop claimer lease.

    The managed sidecar-delivery loop (hermes-managed-host.js) POSTs
    {action: "acquire"} the moment it becomes a live claimer (gateway ok +
    heartbeat + first successful /dispatch/claim — the same point it writes the
    loop-ready marker) and {action: "release"} in its terminal teardown path.

    The lease is the positive deliverability signal that lets the send path tell
    a genuinely-deaf target (released/stale lease) apart from a healthy claimer
    that simply has not polled yet (no lease ever ⇒ fall back to lazy delivery).
    Best-effort/no-throw on the bridge side; tombstoned agents 410 so a removed
    agent's loop stops re-acquiring.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    action = str(body.get("action", "") or "").strip().lower()
    bridge_id = str(body.get("bridgeId", "") or "").strip()
    if action not in {"acquire", "release"}:
        raise HTTPException(400, "action must be 'acquire' or 'release'")
    now = _now()
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        state = await _record_claimer_lease(db, agent_id, action=action, bridge_id=bridge_id, now=now)
        # A lease flip changes deliverability/derived status — invalidate the
        # live-state cache so the next read recomputes immediately.
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        return {"ok": True, "state": state}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/stop-worker")
async def stop_agent_worker(agent_id: str, request: Request):
    """Phase 4: dashboard Stop → agent.status = 'available'.

    Single endpoint that tears down whatever persistent worker the agent
    has (virtual rpc terminal_session, live agent_sessions, terminal
    bindings, runtime_state.virtualTerminalId pointer, turn_busy pulse).
    Bridge-side resources (PiSession pool entry, codex/opencode session
    pools, claude-aify wrapper PTY) get cleaned up by the bridge on its
    next reconcile cycle — the service-side teardown here is
    authoritative for the agent's reported status.

    The agent's persistent identity (registration, capabilities,
    conversation history, session_handle for resume) is preserved.
    Only the live worker lifecycle ends.
    """
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        now = _now()
        runtime_state = _json_loads_or(agent_row["runtime_state"], {}) or {}
        virtual_terminal_id = str(runtime_state.get("virtualTerminalId") or "").strip()
        terminal_payload = None
        if virtual_terminal_id:
            row = await (await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ?",
                (virtual_terminal_id,),
            )).fetchone()
            if row:
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET status = 'stopped',
                        stopped_at = COALESCE(stopped_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, virtual_terminal_id),
                )
                await _append_terminal_event(
                    db,
                    virtual_terminal_id,
                    "agent_worker_stopped",
                    json.dumps({"agentId": agent_id, "requestedAt": now}),
                )
                terminal_payload = _terminal_session_to_dict(row)
            runtime_state.pop("virtualTerminal", None)
            runtime_state.pop("virtualTerminalId", None)
        await db.execute(
            "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
            (json.dumps(runtime_state), now, agent_id),
        )
        # End any live agent_sessions for the agent — they tracked the
        # worker process which is being torn down.
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'ended',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE agent_id = ?
              AND status IN ('starting', 'running', 'recovering', 'restarting', 'cli-takeover', 'managed-warm')
            """,
            (now, now, agent_id),
        )
        # Clear turn_busy.
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 0, '', '', '', ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                turn_busy = 0,
                turn_run_id = '',
                turn_bridge_id = '',
                turn_runtime = '',
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, now),
        )
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_worker_stopped", {"agentId": agent_id, "virtualTerminalId": virtual_terminal_id})
        await _broadcast_agent_status(ws, db, agent_id)
        return {
            "ok": True,
            "agentId": agent_id,
            "virtualTerminalId": virtual_terminal_id,
            "terminal": terminal_payload,
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/turn-start")
async def agent_turn_start(agent_id: str, request: Request):
    """Harness-level turn-START signal — symmetric counterpart to /turn-end.

    Called by per-runtime UserPromptSubmit hooks (claude-aify's
    UserPromptSubmit hook installed via install.sh) when the operator
    types a prompt directly into the resident CLI without going through
    aify-comms's dispatch path. Without this, channel-route dispatches
    correctly flip the agent to "working" but direct CLI typing leaves
    the status at "online" while the assistant is actually mid-turn —
    operator-asked 2026-05-22 to make the two surfaces symmetric.

    Idempotent: refreshes turn_updated_at on every call so the 120s
    server-side staleness window keeps resetting while the assistant
    works.
    """
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute(
            "SELECT id, runtime FROM agents WHERE id = ?", (agent_id,)
        )).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        now = _now()
        runtime = _normalize_runtime(agent_row["runtime"] or "claude-code")
        # If a managed dispatch is already in flight (turn_run_id set,
        # fresh, set by a real bridge), DON'T clobber the dispatch
        # context with our user-prompt-submit attribution. Just refresh
        # turn_updated_at so the existing run linkage keeps the
        # dashboard's "working on subject X" display intact.
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, '', 'user-prompt-submit', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                turn_busy = 1,
                turn_bridge_id = CASE
                    WHEN turn_busy = 1 AND COALESCE(turn_run_id, '') != ''
                         AND COALESCE(turn_bridge_id, '') NOT IN ('', 'user-prompt-submit')
                    THEN turn_bridge_id
                    ELSE 'user-prompt-submit'
                END,
                turn_runtime = excluded.turn_runtime,
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, runtime, now),
        )
        await db.execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            (now, agent_id),
        )
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        return {"ok": True, "agentId": agent_id}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/turn-end")
async def agent_turn_end(agent_id: str, request: Request):
    """Harness-level turn-end signal.

    Called by per-runtime Stop hooks (claude-aify's Stop hook, hermes's
    post_tool_call hook variant, etc.) when the agent has finished its
    current turn at the HARNESS level — i.e., the assistant turn is
    actually over, not just "the agent sent a message." Authoritative
    clear of turn_busy regardless of which bridge originally set it,
    because the harness itself is the source of truth about when its
    own turns end. This is the architectural complement to the
    per-runtime native turn-end signals (codex turn/completed, pi
    agent_end, hermes process exit) that already exist for managed
    runs but were missing for resident claude under claude-channel.js.

    Idempotent: calling when turn_busy is already 0 is a no-op (still
    refreshes turn_updated_at for liveness tracking).
    """
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        now = _now()
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 0, '', '', '', ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                turn_busy = 0,
                turn_run_id = '',
                turn_bridge_id = '',
                turn_runtime = '',
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, now),
        )
        await db.execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            (now, agent_id),
        )
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        return {"ok": True, "agentId": agent_id}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/description")
async def update_agent_description(agent_id: str, req: AgentDescribeRequest, request: Request):
    """Update an agent's team-facing description without re-registering."""
    validate_name(agent_id, "agent ID")
    description = str(req.description or "")
    if len(description) > 2000:
        raise HTTPException(400, "description must be 2000 chars or fewer")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        await db.execute(
            "UPDATE agents SET description = ?, last_seen = ? WHERE id = ?",
            (description, _now(), agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_description_updated", {"agentId": agent_id, "description": description})
        return {"ok": True, "agentId": agent_id, "description": description}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/favorite")
async def update_agent_favorite(agent_id: str, req: AgentFavoriteUpdate, request: Request):
    """Dashboard favorites — pin/unpin an agent in the chat list.

    Operator-set per-deployment flag (not synced across remote
    dashboards). Dashboard renders favorited agents at the top of the
    list and shows a visual marker. Pure metadata — no behavior change.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        flag = 1 if bool(req.favorited) else 0
        await db.execute(
            "UPDATE agents SET favorited = ?, last_seen = ? WHERE id = ?",
            (flag, _now(), agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_favorite_updated", {"agentId": agent_id, "favorited": bool(flag)})
        return {"ok": True, "agentId": agent_id, "favorited": bool(flag)}
    finally:
        await db.close()


@router.get("/agents/{agent_id}/listen")
async def listen_for_messages(agent_id: str, request: Request, timeout: int = Query(300, ge=1, le=600)):
    """Long-poll: blocks until agent has unread messages or timeout. Returns the messages."""
    validate_name(agent_id, "agent ID")

    # Set status to idle (waiting for work)
    db = await get_db()
    try:
        await db.execute("UPDATE agents SET status = 'idle', last_seen = ? WHERE id = ?", (_now(), agent_id))
        await db.commit()
    finally:
        await db.close()

    # Create/get wake-up event for this agent
    if agent_id not in _listen_events:
        _listen_events[agent_id] = asyncio.Event()
    event = _listen_events[agent_id]
    event.clear()

    # Poll for unread messages, waiting on the event
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL",
                (agent_id, agent_id)
            )
            unread = (await cursor.fetchone())[0]
            if unread > 0:
                # Fetch and return the messages (mark as read)
                now = _now()
                mc = await db.execute(
                    "SELECT m.* FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL ORDER BY m.timestamp DESC",
                    (agent_id, agent_id)
                )
                rows = await mc.fetchall()
                messages = []
                for row in rows:
                    msg = {
                        "id": row["id"], "from": row["from_agent"], "type": row["type"],
                        "source": row["source"], "channel": row["channel"],
                        "subject": row["subject"], "body": row["body"],
                        "priority": row["priority"], "timestamp": row["timestamp"],
                        "inReplyTo": row["in_reply_to"],
                        "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                    }
                    # Parent context for replies
                    if row["in_reply_to"]:
                        pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                        parent = await pc.fetchone()
                        if parent:
                            msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
                    messages.append(msg)
                    await db.execute("INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)", (row["id"], agent_id, now))

                # Set status to working
                await db.execute("UPDATE agents SET status = 'working', last_seen = ? WHERE id = ?", (now, agent_id))
                await db.commit()
                return {"total": len(messages), "messages": messages}
        finally:
            await db.close()

        # Wait for wake-up signal or check every 2 seconds
        try:
            await asyncio.wait_for(event.wait(), timeout=2.0)
            event.clear()
        except asyncio.TimeoutError:
            pass

    # Timeout — no messages arrived
    return {"total": 0, "messages": []}


def _wake_agent(agent_id: str):
    """Signal a listening agent that they have new messages."""
    ev = _listen_events.get(agent_id)
    if ev:
        ev.set()


# ─── Dispatch Runs ────────────────────────────────────────────────────────────

@router.post("/dispatch")
async def create_dispatch(req: DispatchRequest, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    if req.mode == "message_only":
        raise HTTPException(400, "Dispatch no longer supports mode='message_only'. Use comms_send for normal live messaging or comms_dispatch without message_only for tracked work.")

    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; dispatch was sent unthreaded.'
            )
        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": [], "runs": []}

        not_started = []
        launchable_recipients = []
        console_recipients = {}
        recipient_rows = {}
        settings = await _load_settings(db)
        for recipient_id in recipients:
            cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
            row = await cursor.fetchone()
            if row:
                row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
            if row:
                recipient_rows[recipient_id] = row
                # Plan 2 (2026-05-25) pi flip: reject new dispatches to a pi
                # agent that's currently mid-flip from resident -> managed.
                # _drain_and_flip_pi_resident_agents will migrate it once
                # any active runs drain (~5s). The operator should retry
                # after the flip completes. Without this gate, dispatches
                # queue against a session_mode the runtime no longer
                # supports.
                if _normalize_runtime(row["runtime"] or "") == "pi":
                    _rs = _json_loads_or(row["runtime_state"], {})
                    if _rs.get("pi_resident_pending_flip"):
                        raise HTTPException(
                            409,
                            f'Agent "{recipient_id}" is migrating from resident '
                            f"to managed (pi flip pending). Retry in a few "
                            f"seconds — the drain loop will flip the agent "
                            f"once any active runs complete."
                        )
            execution_mode = None
            reason = None if row else "agent is not registered"
            if row:
                runtime = _normalize_runtime(row["runtime"] or "generic")
                if req.requestedRuntime and _normalize_runtime(req.requestedRuntime) != runtime:
                    reason = f'requested runtime "{req.requestedRuntime}" does not match registered runtime "{runtime}"'
                elif runtime in _NATIVE_MANAGED_RUNTIMES:
                    # Plan 5 (2026-05-25): pass settings so
                    # _agent_execution_mode can detect wrapper-backed managed
                    # runtimes (managed_via_wrapper) and return
                    # execution_mode='channel'. Without settings the helper
                    # short-circuits to 'managed' (line 1065) and the
                    # wrapper-backed dispatch path never fires.
                    execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                    # Plan 5 follow-up (2026-05-26): the PTY-input
                    # (console_recipients) downgrade below MUST only fire
                    # for execution_mode='managed'. When the helper returns
                    # 'channel' for wrapper-backed codex/hermes, leave
                    # the run as channel-mode so the wrapper PTY's child
                    # bridge can pick it up. Falling through to
                    # console_recipients would route the message via raw PTY
                    # keystrokes — the scrambled-text failure mode the
                    # operator explicitly banned.
                    if (
                        not reason
                        and execution_mode == "channel"
                        and _managed_terminal_backing_enabled(settings)
                        and _managed_via_wrapper_for_runtime(settings, runtime)
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if not console_terminal:
                            reason = f"Managed {runtime} wrapper PTY is unavailable; recover or restart the environment-managed session."
                    if not reason and execution_mode == "managed":
                        if (
                            _managed_terminal_backing_enabled(settings)
                            and _insert_messages_via_console(settings)
                            and runtime not in {"pi", "opencode"}
                        ):
                            console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                            if not console_terminal:
                                console_terminal = await _ensure_managed_pty_for_dispatch(
                                    db,
                                    recipient_id,
                                    runtime=runtime,
                                    settings=settings,
                                    requested_by=req.from_agent,
                                )
                            if console_terminal:
                                console_recipients[recipient_id] = console_terminal
                                execution_mode = None
                            else:
                                reason = await _managed_environment_unavailable_reason(db, row)
                elif runtime in _CHANNEL_MANAGED_RUNTIMES:
                    # Plan 5 (2026-05-25): pass settings (parity with the
                    # NATIVE_MANAGED branch above). _agent_execution_mode
                    # uses settings to gate the wrapper-backed channel route.
                    execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                    if not reason and execution_mode == "channel":
                        reason = await _managed_environment_unavailable_reason(db, row)
                    if not reason and execution_mode == "channel" and _insert_messages_via_console(settings):
                        # PTY-input path — only the opt-in via-console
                        # delivery mode goes through here. Default-false
                        # routing leaves the run launchable and the post-
                        # create _apply_channel_routing_to_claude_runs
                        # flips execution_mode='channel' so claude-channel.js
                        # claims it.
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if console_terminal:
                            console_recipients[recipient_id] = console_terminal
                            execution_mode = None
                        else:
                            reason = "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session."
                    elif reason:
                        execution_mode = None
                else:
                    console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                    if not console_terminal:
                        console_terminal = await _ensure_managed_pty_for_dispatch(
                            db,
                            recipient_id,
                            runtime=runtime,
                            settings=settings,
                            requested_by=req.from_agent,
                        )
                    if console_terminal:
                        console_recipients[recipient_id] = console_terminal
                    else:
                        # Plan 5 (2026-05-25): pass settings — see sibling
                        # branches above for rationale.
                        execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                        if not reason and execution_mode:
                            reason = await _managed_environment_unavailable_reason(db, row)
            if reason or not execution_mode:
                if recipient_id not in console_recipients:
                    not_started.append(_dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable"))
            else:
                launchable_recipients.append((recipient_id, execution_mode))

        if req.mode == "require_start" and not_started:
            details = "; ".join(f"{item['targetAgentId']}: {item['reason']}" for item in not_started)
            return {
                "ok": False,
                "error": f"Active dispatch unavailable for: {details}",
                "recipients": recipients,
                "runs": [],
                "notStarted": not_started,
            }

        message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        source_message_ids = {}
        ts = int(time.time() * 1000)
        for recipient_id in recipients:
            recipient_message_id = f"{message_id}-{recipient_id}" if len(recipients) > 1 else message_id
            source_message_ids[recipient_id] = recipient_message_id
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    recipient_message_id,
                    req.from_agent, recipient_id, "direct", req.type, req.subject, req.body,
                    req.priority, 0 if recipient_id in console_recipients else 1, resolved_in_reply_to, ts
                )
            )
        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=_primary_result_message_id(message_id, recipients),
                reply_type=req.type,
                reply_body=req.body,
            )

        runs = []
        if launchable_recipients:
            require_reply = _dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))
            runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode=req.mode,
                execution_mode="managed",
                requested_runtime=req.requestedRuntime,
                message_id=message_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=req.steer,
                require_reply=require_reply,
            )
            runs = await _finalize_dispatch_runs(db, runs, launchable_recipients, not_started)
            await _apply_channel_routing_to_claude_runs(db, runs, settings)

        console_deliveries = []
        for recipient_id, terminal in console_recipients.items():
            terminal_id = str(terminal["terminal_id"] or "").strip()
            recipient_message_id = source_message_ids.get(recipient_id, message_id)
            terminal_runtime = _normalize_runtime(terminal["runtime"] or "")
            control_id = await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=terminal["environment_id"],
                bridge_id=terminal["bridge_id"] or "",
                action="input",
                requested_by=req.from_agent,
                body=_console_dispatch_input_body(
                    req,
                    recipient_id=recipient_id,
                    message_id=recipient_message_id,
                    bracketed_paste=True,
                ),
            )
            submit_control_id = ""
            await _append_terminal_event(
                db,
                terminal_id,
                "terminal_input_requested",
                json.dumps({
                    "requestedBy": req.from_agent,
                    "controlId": control_id,
                    "submitControlId": submit_control_id,
                    "source": "dispatch",
                    "messageId": recipient_message_id,
                }),
            )
            contract_run_id = await _record_terminal_delivery_contract(
                db,
                source_message_id=recipient_message_id,
                from_agent=req.from_agent,
                recipient_id=recipient_id,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                require_reply=_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type)),
                terminal_id=terminal_id,
                control_id=control_id,
                runtime=terminal["runtime"] or "",
            )
            console_deliveries.append({
                "targetAgentId": recipient_id,
                "terminalId": terminal_id,
                "controlId": control_id,
                "contractRunId": contract_run_id,
                "status": "sent_to_console",
            })

        recipient_info = {}
        for recipient_id in recipients:
            info = await _get_recipient_info(db, recipient_id)
            if info:
                recipient_info[recipient_id] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "dispatch_request", {"from": req.from_agent, "subject": req.subject})
            for run in runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
            for delivery in console_deliveries:
                await ws.broadcast("terminal_control_requested", {"terminalId": delivery["terminalId"], "action": "input"})
        for recipient_id in recipients:
            _wake_agent(recipient_id)

        return {
            "ok": True,
            "messageId": message_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "runs": runs,
            "notStarted": not_started,
            "consoleDeliveries": console_deliveries,
            "warnings": warnings,
        }
    finally:
        await db.close()


@router.post("/dispatch/claim")
async def claim_dispatch(req: DispatchClaimRequest, request: Request):
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        # Plan 5 (2026-05-25): settings is needed below for the
        # _agent_execution_mode call (so the wrapper-backed channel route
        # at line 1047 fires symmetrically with the dispatch-create path).
        claim_settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            tombstone = await _agent_tombstone(db, req.agentId)
            if tombstone:
                await db.rollback()
                raise HTTPException(410, f"Agent '{req.agentId}' was intentionally removed")
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        if req.machineId and agent["machine_id"] and not _machine_ids_same_host(agent["machine_id"], req.machineId):
            await db.rollback()
            return {"ok": True, "run": None}

        agent_runtime = _normalize_runtime(agent["runtime"] or "generic")

        # Mode FSM release signal (Task 4.1, 2026-05-30). A DISPLACED managed
        # sidecar (claude-channel.js / hermes-channel.js, bridgeKind="channel-sidecar")
        # must STOP driving once the operator switches the agent to resident.
        # We surface `release: true` in the claim response so the sidecar exits
        # its poll loop / goes idle. This is the one-driver invariant in action:
        # the managed driver releases so the resident TUI can take the session.
        #
        # driver_state guard (operator-reported 2026-05-31, sc-manager): the
        # original condition was the blunt `session_mode != managed`, which ALSO
        # fired for a NATIVELY-resident agent whose channel sidecar is its SOLE
        # delivery path — so every resident claude/hermes agent's sidecar was told
        # to release and queued runs never got claimed (delivery silently stalled).
        # A live resident driver has driver_state='driving' (set on resident
        # register/claim); a managed→resident switch sets driver_state='idle'
        # (the displaced managed driver, awaiting a resident takeover). Release
        # ONLY when not actively driven, so the resident delivery sidecar keeps
        # claiming for a live resident session.
        if (
            str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            and _normalize_session_mode(agent["session_mode"] or "resident") != "managed"
            and str((agent["driver_state"] if "driver_state" in agent.keys() else "") or "").strip().lower() != "driving"
        ):
            await db.commit()
            return {"ok": True, "run": None, "release": True, "sessionMode": _normalize_session_mode(agent["session_mode"] or "resident")}

        # Self-heal a superseded channel-sidecar (operator-reported 2026-05-31,
        # sc-claude). A managed agent's channel sidecar and the visible TUI's
        # managed-wrapper-child bridge legitimately COEXIST (complementary pair).
        # During managed-PTY churn the sidecar's row briefly goes stale and a
        # wrapper-child registration superseded it (the 5-min-stale clause
        # overrode the complementary-pair carve-out in _record_bridge_registration).
        # Once superseded, _bridge_claim_block_reason permanently BLOCKED the
        # still-live sidecar — and the block fires BEFORE the heartbeat upsert,
        # so it could never recover; queued channel runs were never delivered.
        # A live channel-sidecar poll is proof of life: un-supersede its OWN row
        # so it resumes claiming. The mode-FSM release above (driver_state-gated)
        # is the ONLY legitimate "stop driving" signal for a channel sidecar.
        # KEPT (Task A' #154, 2026-06-01): the 30s liveness beat does NOT revive a
        # superseded bridge (it short-circuits on superseded rows — see
        # test_liveness_beat_does_not_revive_superseded_bridge), so only this
        # claim-path self-heal can un-supersede a still-live sidecar. Removal
        # probe broke test_superseded_channel_sidecar_self_heals_on_claim.
        if str(req.bridgeKind or "").strip().lower() == "channel-sidecar" and req.bridgeId:
            await db.execute(
                """
                UPDATE bridge_instances
                SET superseded_by = '', superseded_at = NULL, last_seen = ?
                WHERE id = ? AND agent_id = ? AND COALESCE(superseded_by, '') != ''
                """,
                (_now(), req.bridgeId, req.agentId),
            )

        # Reject claims from stale stdio bridges. The bridge_instances row
        # catches normal supersession, while runtimeState.bridgeInstanceId
        # catches the more dangerous case where an old process keeps polling
        # after its bridge row has disappeared or been compacted away.
        blocked_by = await _bridge_claim_block_reason(
            db,
            bridge_id=req.bridgeId or "",
            agent_id=req.agentId,
            agent_row=agent,
            execution_modes=req.executionModes or [],
            bridge_kind_hint=req.bridgeKind or "",
        )
        if blocked_by:
            await db.commit()
            return {
                "ok": True,
                "run": None,
                "blockedBy": blocked_by,
            }

        # Update bridge liveness — the claim poll itself is the heartbeat.
        if req.bridgeId:
            is_channel_sidecar_claim = (
                str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            )
            if is_channel_sidecar_claim:
                # Task 1.6b: a standalone channel sidecar (hermes-channel.js /
                # claude-channel.js) has no bridge row until it claims a run, so
                # a plain UPDATE would no-op for an idle poller and status would
                # flap to `available`. Upsert its channel-sidecar liveness row so
                # the continuous idle poll keeps last_seen fresh and
                # `_has_live_channel_sidecar` stays true. Claude is unaffected
                # (its liveness is the wrapper PTY terminal_session) but this is
                # harmless if claude-channel.js also declares the flag.
                await _record_channel_sidecar_heartbeat(
                    db,
                    bridge_id=req.bridgeId,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=agent_runtime,
                    now=_now(),
                )
            else:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                    (_now(), req.bridgeId, req.agentId),
                )

        # Stale-run cleanup.
        #
        # The bridge-side gate (ACTIVE_RUNS in server.js) prevents a live
        # bridge from calling /dispatch/claim while it has work in flight.
        # That only proves the *polling* bridge is idle. Wrapper-backed
        # managed agents have two bridge identities in play: the environment
        # bridge keeps polling, while the wrapper-child bridge owns the
        # active turn. Treat a different owner as stale only after the owner
        # bridge itself stops heartbeating or is superseded.
        active_state = await _get_dispatch_state_for_agent(db, req.agentId)
        active_run = active_state.get("activeRun")
        if active_run:
            owner = (active_run.get("claimBridgeId") or "").strip()
            if owner and owner == req.bridgeId:
                await db.commit()
                return {"ok": True, "run": None, "blockedBy": active_run}
            if owner:
                owner_cursor = await db.execute(
                    """
                    SELECT last_seen, superseded_by, bridge_kind
                    FROM bridge_instances
                    WHERE id = ? AND agent_id = ?
                    """,
                    (owner, req.agentId),
                )
                owner_bridge = await owner_cursor.fetchone()
                owner_superseded_by = str((owner_bridge["superseded_by"] if owner_bridge else "") or "").strip()
                owner_last_seen = _iso_to_epoch(str((owner_bridge["last_seen"] if owner_bridge else "") or ""))
                owner_heartbeat_age = time.time() - owner_last_seen if owner_last_seen else None
                if (
                    owner_bridge
                    and not owner_superseded_by
                    and owner_heartbeat_age is not None
                    and owner_heartbeat_age < ACTIVE_RUN_BRIDGE_STALE_SECONDS
                ):
                    await db.commit()
                    return {
                        "ok": True,
                        "run": None,
                        "blockedBy": {
                            **active_run,
                            "reason": "active_run_owner_bridge_still_heartbeating",
                            "ownerBridgeId": owner,
                            "ownerBridgeKind": str(owner_bridge["bridge_kind"] or ""),
                            "currentBridgeId": req.bridgeId or "",
                            "retryAfterSeconds": max(1, int(ACTIVE_RUN_BRIDGE_STALE_SECONDS - owner_heartbeat_age)),
                            "hint": "The active run owner bridge is still heartbeating; waiting avoids killing a live wrapper-managed turn.",
                        },
                    }
            active_since = _iso_to_epoch(active_run.get("startedAt") or active_run.get("requestedAt"))
            if owner:
                stale_seconds = ACTIVE_RUN_BRIDGE_STALE_SECONDS
                wait_hint = "A previous bridge claimed this run recently. Waiting avoids killing a run that may still complete."
            else:
                stale_seconds = max(300, int(claim_settings.get("active_run_stale_minutes", 30) or 30) * 60)
                wait_hint = "An unowned terminal turn is still within its stale timeout. Waiting avoids interrupting a visible PTY turn."
            active_age = time.time() - active_since if active_since else stale_seconds + 1
            if active_age < stale_seconds:
                await db.commit()
                return {
                    "ok": True,
                    "run": None,
                    "blockedBy": {
                        **active_run,
                        "reason": "active_run_owned_by_previous_bridge",
                        "ownerBridgeId": owner or "",
                        "currentBridgeId": req.bridgeId or "",
                        "retryAfterSeconds": max(1, int(stale_seconds - active_age)),
                        "hint": wait_hint,
                    },
                }
            finished_at = _now()
            owner_label = owner or "unowned"
            await db.execute(
                "UPDATE dispatch_runs SET status = 'failed', summary = ?, finished_at = ? WHERE id = ?",
                (
                    f'Auto-healed: bridge "{owner_label}" replaced by "{req.bridgeId}"',
                    finished_at,
                    active_run["runId"],
                ),
            )
            await _append_dispatch_event(db, active_run["runId"], "auto_heal", f"Stale run cleanup: {owner_label} -> {req.bridgeId}")
            await _fail_pending_controls_for_run(db, active_run["runId"], handled_at=finished_at, response_text=f'Stale run cleaned by live bridge "{req.bridgeId}".')
        owner_cursor = await db.execute(
            """
            SELECT id, environment_id, owner_mode, terminal_id, terminal_status
            FROM agent_sessions
            WHERE agent_id = ?
              AND status IN ('starting','running','recovering','restarting')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (req.agentId,),
        )
        owner_session = await owner_cursor.fetchone()
        supported_modes = {str(mode or "").strip().lower() for mode in (req.executionModes or []) if str(mode or "").strip()}
        # See _CHANNEL_CLAIM_RUNTIMES and _bridge_claim_block_reason: managed
        # wrapper-backed Codex/Hermes channel runs are claimable only by the
        # wrapper PTY child bridge, not the main environment bridge.
        channel_claim = agent_runtime in _CHANNEL_CLAIM_RUNTIMES and "channel" in supported_modes
        if owner_session and str(owner_session["owner_mode"] or "").strip().lower() == "console" and not channel_claim:
            blocked_by_console = await _release_stale_console_owner_for_claim(db, owner_session, req)
            if blocked_by_console:
                await db.commit()
                return {
                    "ok": True,
                    "run": None,
                    "blockedBy": blocked_by_console,
                }
        # Turn-busy claim gate: if the agent is currently mid-turn
        # (turn_busy=1, fresh, within TURN_BUSY_STALE_SECONDS),
        # don't return queued runs. Operator-asked 2026-05-22:
        # "queue should wait until agent stops working" — without this
        # gate, the SENDER's queueIfBusy=true correctly held the run
        # in 'queued' state, but the bridge's next claim cycle picked
        # it up and delivered immediately because the claim endpoint
        # didn't respect turn_busy. Stop hook (or 120s stale window)
        # is the authoritative clear; once that fires, next claim
        # picks up the queued run as designed.
        #
        # CHANNEL/RESIDENT STEER CARVE-OUT (2026-06-02, send-deadlock fix):
        # a channel/resident-mode run to a STEER-capable target (a managed or
        # channelEnabled resident claude — `steer` in _row_capabilities, the
        # same signal used by the send-time steer path) is INJECTED into the
        # agent's input mid-turn; claude queues multiple injects safely and in
        # order. Deferring such a run behind turn_busy was the deadlock: every
        # delivery re-pulses the recipient's turn_busy (claude-channel.js), so a
        # queued rr=0 send never found a 120s gap and waited minutes. So when the
        # target can steer AND a claimable channel/resident run is queued, do NOT
        # defer — fall through and let it claim + inject immediately. The gate is
        # PRESERVED for every other case (non-steer-capable runtimes, managed
        # headless runs) so a genuinely-uninjectable target still waits for the
        # turn to end.
        try:
            tb_row = await (await db.execute(
                "SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
                (req.agentId,),
            )).fetchone()
            if tb_row and int(tb_row["turn_busy"] or 0) == 1:
                tb_epoch = _iso_to_epoch(str(tb_row["turn_updated_at"] or ""))
                if tb_epoch and (datetime.now(timezone.utc).timestamp() - tb_epoch) <= TURN_BUSY_STALE_SECONDS:
                    if not await _has_claimable_steerable_run(
                        db,
                        agent_row=agent,
                        supported_modes=supported_modes,
                        agent_runtime=agent_runtime,
                    ):
                        await db.commit()
                        return {"ok": True, "run": None}
        except Exception:
            # If turn_busy state is unreadable, fall through and let the
            # normal claim flow proceed — better to deliver than block.
            pass

        run_cursor = await db.execute(
            """
            SELECT * FROM dispatch_runs
            WHERE target_agent = ? AND status = 'queued'
            ORDER BY requested_at ASC
            LIMIT 25
            """,
            (req.agentId,)
        )
        runs = await run_cursor.fetchall()
        selected_run = None
        for run in runs:
            run_execution_mode = (run["execution_mode"] or "managed").strip().lower()
            if supported_modes and run_execution_mode not in supported_modes:
                continue
            if run["dispatch_mode"] == "message_only":
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                    (_now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", "Dispatch mode is message_only")
                continue
            requested_runtime = run["requested_runtime"] or ""
            if requested_runtime and _normalize_runtime(requested_runtime) != agent_runtime:
                continue

            # Plan 5 (2026-05-25): pass settings so the wrapper-backed
            # channel route (line 1047) matches what _agent_execution_mode
            # returned when the run was created. Without settings here, the
            # helper short-circuits to 'managed', then line 11258 below sees
            # run.execution_mode='channel' != 'managed' and cancels the run.
            execution_mode, reason = _agent_execution_mode(agent, requested_runtime or None, settings=claim_settings)
            if reason or not execution_mode:
                final_status = "failed" if run["dispatch_mode"] == "require_start" else "cancelled"
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason or "active dispatch unavailable", _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason or "active dispatch unavailable")
                continue
            if (run["execution_mode"] or execution_mode) != execution_mode:
                final_status = "failed" if run["dispatch_mode"] == "require_start" else "cancelled"
                reason = (
                    f'Run execution mode "{run["execution_mode"] or "unknown"}" does not match the '
                    f'current capabilities of agent "{req.agentId}" ({execution_mode}).'
                )
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason, _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason)
                continue

            selected_run = run
            break

        if not selected_run:
            await db.commit()
            return {"ok": True, "run": None}

        claimed_at = _now()
        await db.execute(
            "UPDATE dispatch_runs SET status = 'claimed', claimed_at = ?, claim_machine_id = ?, claim_bridge_id = ?, runtime = ? WHERE id = ?",
            (claimed_at, req.machineId or "", req.bridgeId or "", agent_runtime, selected_run["id"])
        )
        # One-driver FSM (Task 4.1): a managed sidecar that successfully claims a
        # run for a managed agent is the live driver -> mark driving so a
        # cross-mode resident attach is rejected by the collision guard.
        if (
            str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            and _normalize_session_mode(agent["session_mode"] or "resident") == "managed"
        ):
            await db.execute(
                "UPDATE agents SET driver_state = 'driving' WHERE id = ?",
                (req.agentId,),
            )
        await _invalidate_agent_live_state(db, req.agentId)
        await _touch_current_agent_session(
            db,
            req.agentId,
            _json_loads_or(agent["runtime_state"], {}),
            claimed_at,
        )
        marked_read = await _mark_dispatch_source_messages_read(db, selected_run, req.agentId, claimed_at)
        await _append_dispatch_event(db, selected_run["id"], "claimed", f"machine={req.machineId or ''}")
        if marked_read > 1:
            await _append_dispatch_event(db, selected_run["id"], "read_receipts", f"Marked {marked_read} dispatched source messages read")
        await db.commit()

        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_claimed", {"runId": selected_run["id"], "targetAgentId": req.agentId})

        return {
            "ok": True,
            "run": {
                "id": selected_run["id"],
                "messageId": selected_run["message_id"],
                "from": selected_run["from_agent"],
                "targetAgentId": selected_run["target_agent"],
                "type": selected_run["message_type"],
                "subject": selected_run["subject"],
                "body": selected_run["body"],
                "priority": selected_run["priority"],
                "inReplyTo": selected_run["in_reply_to"],
                "status": "claimed",
                "mode": selected_run["dispatch_mode"],
                "executionMode": selected_run["execution_mode"] or "managed",
                "runtime": agent_runtime,
                "requireReply": _row_require_reply(selected_run),
                "conversationContext": await _dispatch_conversation_context(db, selected_run),
                "claimBridgeId": req.bridgeId or "",
                "requestedRuntime": selected_run["requested_runtime"] or None,
                "claimedAt": claimed_at,
            }
        }
    finally:
        await db.close()


@router.get("/dispatch/runs")
async def list_dispatch_runs(
    request: Request,
    agentId: Optional[str] = None,
    fromAgent: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    try:
        repaired_active_runs = await _repair_unusable_active_runs(db)
        if repaired_active_runs:
            await db.commit()
        # Plan 6 follow-up (2026-05-26): Section C's mode-switch audit
        # inserts synthetic `dispatch_runs` rows with dispatch_mode='audit'
        # to satisfy the dispatch_events.run_id FK constraint. Those rows
        # are never claimed/queued/started — they exist only as audit
        # anchors. Hide them from the listing endpoint so the dashboard's
        # dispatch history view doesn't fill with mode_switch_* entries.
        # Audit anchors are still queryable individually via the
        # per-id endpoint and via dispatch_events.
        query = "SELECT * FROM dispatch_runs WHERE (dispatch_mode IS NULL OR dispatch_mode != 'audit')"
        params = []
        if agentId:
            query += " AND target_agent = ?"
            params.append(agentId)
        if fromAgent:
            query += " AND from_agent = ?"
            params.append(fromAgent)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY requested_at DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(query, params)
        runs = []
        for row in await cursor.fetchall():
            blocked_by = None
            if row["status"] == "queued":
                blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
            payload = _serialize_dispatch_run_row(row, blocked_by=blocked_by)
            controls_cursor = await db.execute(
                """
                SELECT id, action, status, source_message_id, response_text
                FROM dispatch_controls
                WHERE run_id = ? AND source_message_id != ''
                ORDER BY requested_at ASC
                LIMIT 50
                """,
                (row["id"],),
            )
            source_controls = [
                {
                    "id": control["id"],
                    "action": control["action"],
                    "status": control["status"],
                    "sourceMessageId": control["source_message_id"],
                    "response": control["response_text"] or "",
                }
                for control in await controls_cursor.fetchall()
            ]
            if source_controls:
                payload["sourceControls"] = source_controls
            runs.append(payload)
        return {"runs": runs}
    finally:
        await db.close()


@router.get("/dispatch/runs/{run_id}/events")
async def list_dispatch_run_events(
    run_id: str,
    limit: int = Query(50, ge=1),
    before: Optional[int] = Query(None, ge=1),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    db = await get_db()
    try:
        run_cursor = await db.execute("SELECT 1 FROM dispatch_runs WHERE id = ?", (run_id,))
        if not await run_cursor.fetchone():
            raise HTTPException(404, f"Run '{run_id}' not found")

        bounded_limit = min(limit, 50)
        params: list[Any] = [run_id]
        cursor_clause = ""
        direction = "DESC" if order == "desc" else "ASC"
        if before is not None:
            cursor_clause = "AND id < ?" if order == "desc" else "AND id > ?"
            params.append(before)
        params.append(bounded_limit + 1)
        events_cursor = await db.execute(
            f"""
            SELECT id, event_type, body, created_at
            FROM dispatch_events
            WHERE run_id = ? {cursor_clause}
            ORDER BY id {direction}
            LIMIT ?
            """,
            tuple(params),
        )
        rows = await events_cursor.fetchall()
        page = rows[:bounded_limit]
        return {
            "ok": True,
            "runId": run_id,
            "events": [
                {
                    "id": str(event["id"]),
                    "type": event["event_type"],
                    "eventType": event["event_type"],
                    "body": event["body"] or "",
                    "createdAt": event["created_at"],
                }
                for event in page
            ],
            "hasMore": len(rows) > bounded_limit,
            "nextBefore": str(page[-1]["id"]) if len(rows) > bounded_limit and page else "",
            "order": order,
            "limit": bounded_limit,
        }
    finally:
        await db.close()


@router.get("/dispatch/runs/{run_id}")
async def get_dispatch_run(run_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")
        ec = await db.execute(
            "SELECT event_type, body, created_at FROM dispatch_events WHERE run_id = ? ORDER BY id ASC LIMIT 200",
            (run_id,)
        )
        events = [
            {"type": event["event_type"], "body": event["body"], "createdAt": event["created_at"]}
            for event in await ec.fetchall()
        ]
        cc = await db.execute(
            """
            SELECT id, from_agent, action, body, status, response_text, source_message_id, requested_at, claimed_at, handled_at
            FROM dispatch_controls WHERE run_id = ? ORDER BY requested_at ASC LIMIT 200
            """,
            (run_id,)
        )
        controls = [
            {
                "id": control["id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "status": control["status"],
                "response": control["response_text"],
                "sourceMessageId": control["source_message_id"],
                "requestedAt": control["requested_at"],
                "claimedAt": control["claimed_at"],
                "handledAt": control["handled_at"],
            }
            for control in await cc.fetchall()
        ]
        blocked_by = None
        if row["status"] == "queued":
            blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
        return {
            "run": _serialize_dispatch_run_row(
                row,
                blocked_by=blocked_by,
                include_body=True,
                include_events=events,
                include_controls=controls,
            )
        }
    finally:
        await db.close()


async def _close_orphaned_managed_runs(db, *, limit: int = 200) -> list[dict[str, str]]:
    """Close managed/channel/resident dispatch_runs whose owning bridge
    didn't report a terminal status within `active_managed_run_stale_minutes`.

    Operator-reported case (2026-05-22): hermes-test's createHermesController
    spawn failed (provider missing) but the dispatch_run lingered in
    'running' state for 30 minutes before the generic 30-min stale repair
    caught it. The bridge's failure-PATCH may have hit a transient
    connection error and was logged-but-dropped — bridge-side retry
    logic now catches most of these, but a service-side safety net is
    still worth having for cases where the bridge crashed entirely.

    Only called from the periodic reconciler — NOT from preflight —
    because preflight's stale-repair call uses a different (terminal-
    only) discriminator that older steer-preflight tests pin against.
    This function catches orphaned runs regardless of dispatch_mode:
    a terminal-mode run with empty claim_bridge_id means the wrapper
    PTY backing was supposed to drive it but the bridge that spawned
    the PTY is gone — same orphan condition as managed-mode runs,
    deserves the same fast cleanup. Operator-reported 2026-05-22:
    hermes-test queued run sat blocked behind a terminal-mode running
    run with empty bridge_id for 45+ min waiting for the 30-min
    generic stale reaper.
    """
    settings = await _load_settings(db)
    stale_minutes = int(settings.get("active_managed_run_stale_minutes", 5) or 5)
    stale_seconds = max(60, stale_minutes * 60)
    cutoff_param = f"-{stale_seconds} seconds"
    # Absolute wall-clock ceiling (FIX 5, 2026-06-01): applied regardless of
    # bridge liveness, so a run pinned `working` by a live bridge whose inner
    # controller died is still aged out. Always >= stale_seconds so it never
    # narrows the existing bridge-staleness reaper. Keyed on no-progress for the
    # ceiling window (same dispatch_events check) so progressing runs are safe.
    ceiling_minutes = int(settings.get("active_managed_run_wall_ceiling_minutes", 30) or 30)
    ceiling_seconds = max(stale_seconds, ceiling_minutes * 60)
    ceiling_param = f"-{ceiling_seconds} seconds"
    # Defense against false-positive reaping (code review C1, 2026-05-22):
    # an orphan candidate must satisfy ALL of:
    #   1. status claimed/running
    #   2. claim_bridge_id is empty (no bridge took ownership) OR the
    #      named bridge_instance is gone/stale (operator-reported
    #      2026-05-23: sc-coder's hermes managed run sat at "running"
    #      for 50+ min because claim_bridge_id pointed at a bridge
    #      that had since gone stale — original "claim_bridge_id = ''"
    #      check missed this case. A bridge that hasn't heartbeated
    #      within stale_seconds is dead from the dispatcher's POV;
    #      runs it claimed are orphaned).
    #   3. started_at + stale_seconds is in the past
    #   4. NO recent dispatch_events of PROGRESS kind (run hasn't
    #      progressed since the cutoff). reply_reminder_skipped is a
    #      service-side METADATA event the reminder loop emits about
    #      the run, not progress FROM the runtime — exclude it (same
    #      operator-report: reply_reminder_skipped fired every minute,
    #      kept resetting this cutoff window even after the controller
    #      had died).
    cursor = await db.execute(
        """
        SELECT id, target_agent, subject, started_at, requested_at, execution_mode, dispatch_mode, claim_bridge_id
        FROM dispatch_runs r
        WHERE r.status IN ('claimed', 'running')
          AND (
            -- Branch 1: no owning bridge (empty OR stale) + no progress for the
            -- stale window — the original fast bridge-liveness reaper.
            (
              (
                COALESCE(r.claim_bridge_id, '') = ''
                OR NOT EXISTS (
                  SELECT 1 FROM bridge_instances bi
                  WHERE bi.id = r.claim_bridge_id
                    AND datetime(bi.last_seen) > datetime('now', ?)
                )
              )
              AND datetime(COALESCE(r.started_at, r.requested_at)) <= datetime('now', ?)
              AND NOT EXISTS (
                SELECT 1 FROM dispatch_events de
                WHERE de.run_id = r.id
                  AND datetime(de.created_at) > datetime('now', ?)
                  AND de.event_type NOT IN ('reply_reminder_skipped')
              )
            )
            -- Branch 2 (FIX 5): absolute wall-clock ceiling, applied REGARDLESS of
            -- bridge liveness. A run that has made no progress for the ceiling
            -- window is aged out even if the bridge is still heartbeating (the
            -- inner controller died without PATCHing the run terminal).
            OR (
              datetime(COALESCE(r.started_at, r.claimed_at, r.requested_at)) <= datetime('now', ?)
              AND NOT EXISTS (
                SELECT 1 FROM dispatch_events de
                WHERE de.run_id = r.id
                  AND datetime(de.created_at) > datetime('now', ?)
                  AND de.event_type NOT IN ('reply_reminder_skipped')
              )
            )
          )
        ORDER BY r.requested_at ASC
        LIMIT ?
        """,
        (cutoff_param, cutoff_param, cutoff_param, ceiling_param, ceiling_param, limit),
    )
    rows = await cursor.fetchall()
    closed: list[dict[str, str]] = []
    now = _now()
    for row in rows:
        run_id = str(row["id"] or "").strip()
        target_agent = str(row["target_agent"] or "").strip()
        dispatch_mode = str(row["dispatch_mode"] or "").strip()
        execution_mode = str(row["execution_mode"] or "").strip()
        if not run_id:
            continue
        reason = (
            f"Active run (dispatch_mode={dispatch_mode or '(default)'}, "
            f"execution_mode={execution_mode or '(default)'}) has no owning bridge "
            f"and made no progress for {stale_seconds}s, or exceeded the "
            f"{ceiling_seconds}s wall-clock ceiling with no progress — bridge "
            f"crashed, the inner controller died without reporting, the failure "
            f"PATCH was dropped, or the wrapper PTY never claimed."
        )
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed',
                error_text = ?,
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ?
            """,
            (reason, now, run_id),
        )
        await _append_dispatch_event(db, run_id, "failed", reason)
        if target_agent:
            # Clear turn_busy so the agent's status falls back to
            # available/online instead of staying "working" via stale
            # heartbeat.
            await db.execute(
                """
                INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
                VALUES (?, 0, '', '', '', ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    turn_busy = 0,
                    turn_run_id = '',
                    turn_bridge_id = '',
                    turn_runtime = '',
                    turn_updated_at = excluded.turn_updated_at
                """,
                (target_agent, now),
            )
            await _invalidate_agent_live_state(db, target_agent)
        closed.append({"runId": run_id, "agentId": target_agent})
    return closed


async def _reconcile_duplicate_resident_sessions(db, *, limit: int = 500) -> int:
    """Reconcile (2026-06-03): a resident agent should have exactly ONE live
    resident session, but the resident session id is a hash of session_handle, so
    each relaunch with a new native handle minted a NEW resident_* row while older
    ones stayed 'running' — the dashboard showed duplicate/stale resident sessions
    the operator could not tell apart ("no way of knowing what to delete"). The
    register-time dedup only retires siblings on a FRESH register; this collapses
    the EXISTING duplicates: keep the most-recently-seen resident session per
    agent, retire the rest. Returns rows retired.

    LIVE-SESSION GUARD (HAZARD 2 fix, 2026-06-03): a resident agent_sessions row's
    last_seen is FROZEN at register time — the 30s heartbeat updates agents /
    bridge_instances, NOT agent_sessions — so ranking siblings by `last_seen DESC`
    can keep a dead-but-newer row and retire a LIVE one. We therefore NEVER retire a
    non-survivor sibling whose owning bridge (owner_bridge_id) is still FRESH: a
    fresh, non-superseded bridge_instances row (last_seen within
    resident_lease_seconds) proves that session is still live. The freshest session
    per agent is still the survivor, but among the rest we retire ONLY those whose
    owning bridge is stale/gone — a sibling with a fresh bridge is LEFT ALONE (a
    transient duplicate is safer than retiring a live session)."""
    settings = await _load_settings(db)
    lease_seconds = int(settings.get("resident_lease_seconds", 150) or 150)
    live_states = ("running", "attached", "active", "idle", "starting", "recovering")
    state_ph = ",".join("?" for _ in live_states)
    rows = await (
        await db.execute(
            f"""
            SELECT id, agent_id, owner_bridge_id
            FROM agent_sessions
            WHERE mode = 'resident' AND status IN ({state_ph})
            ORDER BY agent_id ASC, last_seen DESC, rowid DESC
            """,
            list(live_states),
        )
    ).fetchall()

    async def _owner_bridge_is_fresh(owner_bridge_id: str, agent_id: str) -> bool:
        """True when the session's owning bridge has a fresh, non-superseded
        bridge_instances row (last_seen within the resident lease) — i.e. the
        session is still live and must NOT be retired as a duplicate."""
        bid = str(owner_bridge_id or "").strip()
        if not bid:
            return False
        try:
            cur = await db.execute(
                """
                SELECT 1 FROM bridge_instances
                WHERE id = ? AND agent_id = ?
                  AND COALESCE(superseded_by, '') = ''
                  AND datetime(last_seen) > datetime('now', ?)
                LIMIT 1
                """,
                (bid, agent_id, f"-{int(lease_seconds)} seconds"),
            )
            return (await cur.fetchone()) is not None
        except Exception:
            return False

    # Group each agent's live resident sessions (rows arrive freshest-first by the
    # ORDER BY). Annotate each with whether its owning bridge is still fresh.
    per_agent: dict[str, list[dict]] = {}
    for row in rows:
        agent_id = str(row["agent_id"] or "")
        keys = row.keys()
        owner_bridge_id = str(row["owner_bridge_id"] if "owner_bridge_id" in keys else "")
        per_agent.setdefault(agent_id, []).append(
            {
                "id": str(row["id"]),
                "owner_bridge_id": owner_bridge_id,
                "bridge_fresh": await _owner_bridge_is_fresh(owner_bridge_id, agent_id),
            }
        )

    retire: list[str] = []
    for agent_id, sessions in per_agent.items():
        if len(sessions) < 2:
            continue  # single session → no-op
        # Survivor selection: prefer a session whose owning bridge is still FRESH
        # (a LIVE session must always win over a dead-but-newer sibling — the
        # resident last_seen is frozen at register time and can rank a dead row
        # newest). Among equal liveness the SQL last_seen order already put the
        # freshest first, so the first live session (or the first row overall when
        # none are live) is the survivor.
        survivor = next((s for s in sessions if s["bridge_fresh"]), sessions[0])
        for s in sessions:
            if s["id"] == survivor["id"]:
                continue
            # Retire a non-survivor ONLY when its owning bridge is stale/gone — a
            # still-fresh bridge means it's a LIVE session, left alone (a transient
            # duplicate is safer than retiring a live session).
            if s["bridge_fresh"]:
                continue
            retire.append(s["id"])
    retire = retire[:limit]
    if not retire:
        return 0
    now = _now()
    id_ph = ",".join("?" for _ in retire)
    await db.execute(
        f"""
        UPDATE agent_sessions
        SET status = 'stopped', ended_at = ?
        WHERE id IN ({id_ph}) AND status IN ({state_ph})
        """,
        [now, *retire, *live_states],
    )
    await db.commit()
    return len(retire)


async def _clear_turn_busy_for_dead_bridges(db, *, limit: int = 200) -> list[dict[str, str]]:
    """Clear a stuck turn_busy=1 whose owning bridge (turn_bridge_id) is dead.

    BUG 1 (2026-06-03): a managed delivery loop / resident channel-sidecar that
    sets turn_busy=1 on submit (hermes-managed-host.js / claude-channel.js) clears
    it on a turn-END EVENT (gateway idle, /turn-end). When that loop process DIES
    (terminal closed, crash) it fires NO turn-end event, so turn_busy sticks until
    the long TURN_BUSY_BACKSTOP_SECONDS ceiling (~30 min) and the agent falsely
    shows `working` the whole time. (Confirmed live: ci-senior-dev stuck `working`,
    turn_bridge_id `hermes-managed-host-wsl:laputa-ci-senior-dev`, turn_updated_at
    ~174s ago, that loop process gone.)

    This is the DEAD-CLAIMER complement to the pure-event turn model — NOT a
    staleness window on normal `working`. It clears turn_busy ONLY when the bridge
    that SET it is no longer live, using the SAME staleness definition as the
    orphaned-claim requeue (ACTIVE_RUN_BRIDGE_STALE_SECONDS heartbeat window):

      1. turn_busy = 1 (the agent is marked mid-turn), AND
      2. turn_bridge_id is non-empty (an identifiable owning bridge — an empty
         owner is a harness/Stop-hook turn, left to the existing 30-min ceiling so
         a genuinely-working resident is never cut off), AND
      3. that turn_bridge_id is NOT a fresh bridge_instances row — either no such
         row exists (superseded-away / never-registered) OR its last_seen is past
         the stale window (the loop stopped heartbeating ⇒ dead).

    A bridge whose last_seen is fresh is genuinely mid-delivery — left untouched
    (the running turn keeps `working`). For each match: zero turn_busy via the
    SAME write the /turn-end endpoint uses and invalidate the agent's live-state
    cache so the false `working` clears immediately. ANTI-FEEDBACK-LOOP safe: this
    only ever CLEARS, keyed on the bridge's heartbeat truth, never on the server's
    derived status.
    """
    stale_param = f"-{ACTIVE_RUN_BRIDGE_STALE_SECONDS} seconds"
    cursor = await db.execute(
        """
        SELECT ats.agent_id, ats.turn_bridge_id
        FROM agent_turn_state ats
        WHERE ats.turn_busy = 1
          AND COALESCE(ats.turn_bridge_id, '') != ''
          AND NOT EXISTS (
            SELECT 1 FROM bridge_instances bi
            WHERE bi.id = ats.turn_bridge_id
              AND COALESCE(bi.agent_id, '') = ats.agent_id
              AND datetime(bi.last_seen) > datetime('now', ?)
          )
        ORDER BY ats.turn_updated_at ASC
        LIMIT ?
        """,
        (stale_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    cleared: list[dict[str, str]] = []
    now = _now()
    for row in rows:
        agent_id = str(row["agent_id"] or "").strip()
        dead_bridge = str(row["turn_bridge_id"] or "").strip() or "(none)"
        if not agent_id:
            continue
        await db.execute(
            """
            UPDATE agent_turn_state
            SET turn_busy = 0,
                turn_run_id = '',
                turn_bridge_id = '',
                turn_runtime = '',
                turn_updated_at = ?
            WHERE agent_id = ?
            """,
            (now, agent_id),
        )
        await _invalidate_agent_live_state(db, agent_id)
        cleared.append({"agentId": agent_id, "deadBridgeId": dead_bridge})
    return cleared


async def _requeue_orphaned_claimed_runs(db, *, grace_seconds: int = 90, limit: int = 200) -> list[dict[str, str]]:
    """Requeue dispatch_runs stranded at 'claimed' by a dead claiming bridge.

    Confirmed live bug (2026-06-02): a bridge claims a run (status -> 'claimed')
    and then dies/restarts (wrapper restart, all hermes.exe killed) BEFORE it
    transitions the run claimed -> delivered. The dead bridge never delivers, a
    NEW bridge will NOT re-claim an already-'claimed' run, so the run is stranded:
    the agent shows falsely busy/working, the message never reaches the console,
    and the sender never gets a reply. (Observed: 3 hermes [STATE CHECK] runs
    stuck at 'claimed' for 15+ min — a `claimed` event, NO `delivered` event,
    only repeated `reply_reminder_skipped "target is busy"`.)

    The existing reapers don't cover this promptly:
      - `_repair_unusable_active_runs` skips a run unless it is the agent's
        CURRENT active run; an orphaned claim by a dead bridge isn't current.
      - `_close_orphaned_managed_runs` only acts after a long stale window /
        wall-clock ceiling, and it FAILS the run rather than recovering it.

    This recovers fast and non-destructively: a run is requeued only when ALL of:
      1. status = 'claimed' (NOT delivered/running/terminal — those reached the
         agent; leave them to the existing reapers),
      2. claimed_at is older than `grace_seconds` ago (long enough that a live
         bridge would have transitioned claimed -> delivered in seconds; short
         enough to recover fast without racing an in-flight delivery),
      3. there is NO `delivered` dispatch_event for the run (never delivered),
      4. the `claim_bridge_id` is NOT a fresh/live bridge_instances row — uses the
         SAME staleness definition as the active-run reaper
         (ACTIVE_RUN_BRIDGE_STALE_SECONDS heartbeat window). An empty
         claim_bridge_id also qualifies (no owner at all).

    GUARD: a claimed run whose claim bridge IS fresh is genuinely delivering right
    now — it is left untouched.

    For each match: requeue it (status='queued', clear claim_bridge_id /
    claim_machine_id / claimed_at), append a `requeued_orphaned_claim` event noting
    the dead bridge id, and invalidate the agent's live-state cache so the false
    busy/working status clears. A live bridge then re-claims + delivers.
    """
    grace_param = f"-{max(1, int(grace_seconds))} seconds"
    stale_param = f"-{ACTIVE_RUN_BRIDGE_STALE_SECONDS} seconds"
    cursor = await db.execute(
        """
        SELECT id, target_agent, claim_bridge_id
        FROM dispatch_runs r
        WHERE r.status = 'claimed'
          AND COALESCE(r.claimed_at, '') != ''
          AND datetime(r.claimed_at) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM dispatch_events de
            WHERE de.run_id = r.id AND de.event_type = 'delivered'
          )
          AND (
            COALESCE(r.claim_bridge_id, '') = ''
            OR NOT EXISTS (
              SELECT 1 FROM bridge_instances bi
              WHERE bi.id = r.claim_bridge_id
                AND datetime(bi.last_seen) > datetime('now', ?)
            )
          )
        ORDER BY r.claimed_at ASC
        LIMIT ?
        """,
        (grace_param, stale_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    requeued: list[dict[str, str]] = []
    for row in rows:
        run_id = str(row["id"] or "").strip()
        target_agent = str(row["target_agent"] or "").strip()
        dead_bridge = str(row["claim_bridge_id"] or "").strip() or "(none)"
        if not run_id:
            continue
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'queued',
                claim_bridge_id = '',
                claim_machine_id = '',
                claimed_at = ''
            WHERE id = ?
            """,
            (run_id,),
        )
        await _append_dispatch_event(
            db,
            run_id,
            "requeued_orphaned_claim",
            f"Requeued: claim bridge '{dead_bridge}' is dead/stale and the run was "
            f"never delivered (stranded at 'claimed' >{grace_seconds}s). A live "
            f"bridge will re-claim.",
        )
        if target_agent:
            await _invalidate_agent_live_state(db, target_agent)
        requeued.append({"runId": run_id, "agentId": target_agent})
    return requeued


async def _agent_has_live_claimer(db, agent_row, *, settings: Optional[dict[str, Any]] = None) -> bool:
    """WS3 (2026-06-02): True when SOME process can claim + deliver a dispatch to
    this agent right now — the runtime-agnostic "live claimer" deliverability
    predicate used by the queued-run backstop (Task 3.2). (Task 3.3 deaf-target
    fail-fast was BLOCKED — see report — because a healthy wrapper-backed managed
    agent legitimately has a live console but no yet-registered claimer before its
    first /dispatch/claim poll, so this predicate cannot distinguish a deaf target
    from a not-yet-polled-healthy one at SEND time. The backstop reaper applies it
    only AFTER a long age window, where that ambiguity has resolved.)

    A live claimer is one of:
      - managed sidecar-delivery runtimes (claude-code / hermes): a fresh,
        non-superseded channel-sidecar bridge heartbeat (the claude-channel.js /
        hermes delivery loop that actually claims) — the SAME signal as the
        Task 3.1 status gate.
      - resident: a fresh resident bridge (its MCP bridge or its channel sidecar).
      - native managed (codex / pi / opencode): any fresh, non-superseded
        bridge_instances row for the agent (the managed env bridge / RPC worker
        that claims via /dispatch/claim).

    NOTE deliberately distinct from "available for cold lazy-autostart": a managed
    agent that is registered but has NO worker yet has no claimer here, but the
    send path still queues to it so the bridge can spawn-on-claim. This predicate
    only proves a claimer is ALIVE RIGHT NOW — callers decide whether absence is
    a fail-fast (up-but-deaf) or a benign cold start.
    """
    if agent_row is None:
        return False
    settings = settings or await _load_settings(db)
    runtime = _normalize_runtime(agent_row["runtime"] or "")
    session_mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    if session_mode == "resident":
        return await _resident_bridge_is_fresh(
            db, agent_row, lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150)
        )
    # Managed sidecar-delivery runtimes: the channel-sidecar / delivery loop IS
    # the claimer. WS5 Task 5.1 (2026-06-02): PREFER the explicit claimer lease.
    # A lease is the positive "the loop is a live claimer right now" signal the
    # delivery loop POSTs on ready and clears on teardown — it resolves the
    # lazy-claim ambiguity that BLOCKED the Task 3.3/5.1b deaf-target fail-fast.
    # Precedence:
    #   1. A lease has been recorded ⇒ the lease is AUTHORITATIVE:
    #        acquired+fresh ⇒ deliverable; released/stale ⇒ NOT deliverable
    #        (immediately — no waiting for the 180s sidecar staleness window).
    #   2. No lease has EVER been recorded ⇒ fall back to the channel-sidecar
    #        heartbeat (pre-existing/older loops + the lazy-claim contract: a
    #        not-yet-polled healthy claimer must NOT be treated as deaf).
    if runtime in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES:
        if await _has_recorded_claimer_lease(db, agent_row["id"]):
            return await _has_live_claimer_lease(db, agent_row["id"])
        return await _has_live_channel_sidecar(db, agent_row["id"])
    # Native managed (codex / pi / opencode): a fresh, non-superseded bridge row
    # for the agent is the claiming worker. Channel sidecar also counts (defensive).
    if await _has_live_channel_sidecar(db, agent_row["id"]):
        return True
    try:
        stale_param = f"-{ACTIVE_RUN_BRIDGE_STALE_SECONDS} seconds"
        cursor = await db.execute(
            """
            SELECT 1 FROM bridge_instances
            WHERE agent_id = ?
              AND COALESCE(superseded_by, '') = ''
              AND datetime(last_seen) > datetime('now', ?)
            LIMIT 1
            """,
            (agent_row["id"], stale_param),
        )
        return await cursor.fetchone() is not None
    except Exception:
        return False


async def _mirror_undeliverable_queued_run_to_sender(db, row, *, reason: str) -> Optional[str]:
    """Write a reply/handoff message from the target back to the original sender
    so an undeliverable queued run (Task 3.2) surfaces instead of vanishing.

    Mirrors the shape of `_mirror_missing_dispatch_handoff` but works for a
    QUEUED run that never reached the agent (no result handoff path applies).
    Skips dashboard senders (the dashboard reads the failed run directly).
    """
    from_agent = str((row["target_agent"] if row else "") or "").strip()
    to_agent = str((row["from_agent"] if row else "") or "").strip()
    if not to_agent or to_agent == "dashboard" or not from_agent:
        return None
    subject = str((row["subject"] if row else "") or (row["id"] if row else "") or "dispatch").strip()
    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    body = (
        "Your queued message was never delivered: the target has no live worker "
        f"(no live claimer) and the run was failed by the queued-run backstop.\n\n{reason}"
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
            "error",
            f"[NOT DELIVERED] {subject}",
            body,
            str((row["priority"] if row else "") or "normal"),
            0,
            str((row["message_id"] if row and "message_id" in row.keys() else "") or "") or None,
            ts,
        ),
    )
    return message_id


async def _reap_undeliverable_queued_runs(db, *, backstop_seconds: Optional[int] = None, limit: int = 200) -> list[dict[str, str]]:
    """WS3 Task 3.2 (2026-06-02): backstop reaper for `queued` dispatch_runs that
    no other reaper covers.

    The existing reapers select `claimed`/`running`/`delivered` only — a `queued`
    run whose target has NO live claimer is invisible to all of them. It piles up
    in the merged buffer until `_DISPATCH_BUFFER_CAP`, then NEW sends hard-reject
    with `buffer_full`. Only an agent-delete drains it. This reaper closes that
    gap: a queued run older than `queued_run_backstop_seconds` whose target is NOT
    deliverable (no live claimer, same predicate as the Task 3.1 status gate /
    Task 3.3 fail-fast) is FAILED with an actionable error, mirrored back to the
    sender, and the target's status cache is invalidated.

    GUARD: a queued run whose target HAS a live claimer is left alone — it will be
    claimed and delivered on the next poll. A run inside the backstop window is
    also left alone (a cold `available` agent may still lazy-autostart on claim).
    """
    settings = await _load_settings(db)
    if backstop_seconds is None:
        backstop_seconds = int(settings.get("queued_run_backstop_seconds", DEFAULT_SETTINGS["queued_run_backstop_seconds"]) or 180)
    backstop_seconds = max(30, int(backstop_seconds))
    cutoff_param = f"-{backstop_seconds} seconds"
    # A run that was just requeued from an orphaned claim (_requeue_orphaned_claimed_runs)
    # keeps its original (old) requested_at, so it would trip this backstop in the
    # SAME reconcile pass and defeat the requeue. Such a run HAD a live claimer
    # once; give it a fresh backstop window to be re-claimed by excluding any
    # queued run with a `requeued_orphaned_claim` event newer than the cutoff.
    cursor = await db.execute(
        """
        SELECT id, target_agent, from_agent, subject, message_id, priority, requested_at
        FROM dispatch_runs r
        WHERE r.status = 'queued'
          AND datetime(COALESCE(r.requested_at, '')) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM dispatch_events de
            WHERE de.run_id = r.id
              AND de.event_type = 'requeued_orphaned_claim'
              AND datetime(de.created_at) > datetime('now', ?)
          )
        ORDER BY r.requested_at ASC
        LIMIT ?
        """,
        (cutoff_param, cutoff_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    reaped: list[dict[str, str]] = []
    now = _now()
    for row in rows:
        run_id = str(row["id"] or "").strip()
        target_agent = str(row["target_agent"] or "").strip()
        if not run_id or not target_agent:
            continue
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (target_agent,))).fetchone()
        if agent_row is None:
            # Tombstoned target — its runs are drained by agent-delete; skip here.
            continue
        if await _agent_has_live_claimer(db, agent_row, settings=settings):
            # Deliverable — a live claimer will pick it up on the next poll.
            continue
        reason = (
            f"Queued for >{backstop_seconds}s with no live claimer for target "
            f'"{target_agent}" (no live channel sidecar / no claiming bridge). The '
            f"agent is up-but-deaf or never started a worker — failed by the "
            f"queued-run backstop so the send does not pile up to buffer_full. "
            f"Restart the agent's worker (managed: respawn its delivery loop / "
            f"console; resident: relaunch its *-aify wrapper), then resend."
        )
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed',
                error_text = ?,
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ?
            """,
            (reason, now, run_id),
        )
        await _append_dispatch_event(db, run_id, "failed", reason)
        await _mirror_undeliverable_queued_run_to_sender(db, row, reason=reason)
        await _invalidate_agent_live_state(db, target_agent)
        reaped.append({"runId": run_id, "agentId": target_agent})
    return reaped


async def _close_idle_virtual_rpc_workers(db, *, limit: int = 200) -> list[dict[str, str]]:
    """Auto-close managed worker terminals idle longer than configured."""
    settings = await _load_settings(db)
    minutes = int(settings.get("worker_idle_close_minutes", 0) or 0)
    if minutes <= 0 or not bool(settings.get("worker_idle_close_enabled", False)):
        return []
    cursor = await db.execute(
        f"""
        SELECT
          t.id,
          t.agent_id,
          t.command,
          t.environment_id,
          t.bridge_id,
          s.id AS agent_session_id
        FROM terminal_sessions t
        LEFT JOIN agent_sessions s ON s.id = t.session_id
        LEFT JOIN agents a ON a.id = t.agent_id
        WHERE t.status IN ('starting', 'attached', 'running', 'recovering', 'active', 'idle')
          AND (
            t.command IN ({",".join("?" for _ in VIRTUAL_RPC_COMMAND_SET)})
            OR t.command LIKE '%-aify%'
            OR t.command LIKE 'opencode%'
          )
          AND (
            COALESCE(a.session_mode, '') = 'managed'
            OR COALESCE(s.owner_mode, '') = 'managed'
            OR COALESCE(s.mode, '') LIKE 'managed%'
          )
          AND datetime(t.updated_at) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM dispatch_runs r
            WHERE r.target_agent = t.agent_id
              AND (
                r.status IN ('queued', 'claimed', 'running')
                OR (r.status = 'delivered' AND COALESCE(r.require_reply, 0) = 1)
              )
          )
        ORDER BY t.updated_at ASC
        LIMIT ?
        """,
        (*VIRTUAL_RPC_COMMAND_SET, f"-{minutes} minutes", limit),
    )
    rows = await cursor.fetchall()
    now = _now()
    closed: list[dict[str, str]] = []
    for row in rows:
        terminal_id = str(row["id"] or "").strip()
        owner_agent = str(row["agent_id"] or "").strip()
        command = str(row["command"] or "").strip()
        if not terminal_id:
            continue
        is_virtual_rpc = command in VIRTUAL_RPC_COMMAND_SET
        has_bridge_owner = bool(str(row["environment_id"] or "").strip() and str(row["bridge_id"] or "").strip())
        next_status = "stopped" if is_virtual_rpc or not has_bridge_owner else "stopping"
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = ?,
                stopped_at = CASE WHEN ? = 'stopped' THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                updated_at = ?,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (
                next_status,
                next_status,
                now,
                now,
                f"Auto-closed: idle longer than worker_idle_close_minutes={minutes}.",
                terminal_id,
            ),
        )
        if not is_virtual_rpc and has_bridge_owner:
            await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=str(row["environment_id"] or "").strip(),
                bridge_id=str(row["bridge_id"] or "").strip(),
                action="stop",
                requested_by="auto-close-idle-worker",
            )
        await _append_terminal_event(
            db,
            terminal_id,
            "managed_worker_auto_closed_idle",
            json.dumps({"agentId": owner_agent, "idleMinutes": minutes, "status": next_status}),
        )
        session_id = str(row["agent_session_id"] or "").strip()
        if session_id:
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (next_status, now, session_id),
            )
        if owner_agent:
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (owner_agent,))).fetchone()
            if agent_row:
                rs = _json_loads_or(agent_row["runtime_state"], {}) or {}
                changed = False
                if str(rs.get("virtualTerminalId") or "").strip() == terminal_id:
                    rs.pop("virtualTerminal", None)
                    rs.pop("virtualTerminalId", None)
                    changed = True
                if str(rs.get("terminalId") or "").strip() == terminal_id:
                    rs.pop("terminalId", None)
                    changed = True
                if changed:
                    await db.execute(
                        "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                        (json.dumps(rs), now, owner_agent),
                    )
            await _invalidate_agent_live_state(db, owner_agent)
        closed.append({"terminalId": terminal_id, "agentId": owner_agent})
    return closed


async def _close_reconcilable_delivered_runs(
    db,
    *,
    limit: int = 500,
    stale_hours: int = 24,
    channel_stale_minutes: int = 30,
) -> list[dict[str, str]]:
    # Three classes of reconcilable lingering 'delivered' runs:
    # 1. Any with result_message_id already set (reply landed but path
    #    that linked it didn't close the run — close now).
    # 2. require_reply=0 runs older than `stale_hours` (info-only, no
    #    reply expected, should have been auto-completed).
    # 3. require_reply=1 + orphaned (no in-flight runs AND no alive
    #    session) older than `stale_hours` — the agent that owed the
    #    reply is gone.
    # 4. Channel/resident execution_mode + require_reply=1 older than
    #    `channel_stale_minutes` (default 30) — these are claude-channel.js
    #    deliveries; the wrapper does NOT preserve in-memory dispatch
    #    state across restarts, so a 'delivered' channel run older than
    #    30 minutes that the bridge never wrote a reply for almost
    #    certainly fell on the floor across a wrapper restart. Without
    #    this, sc-claude-style "agent showing working from before
    #    restart" persists indefinitely once the agent has any live
    #    session (the orphan rule's session check passes).
    cursor = await db.execute(
        """
        SELECT id, result_message_id, require_reply, requested_at
        FROM dispatch_runs
        WHERE status = 'delivered'
          AND COALESCE(finished_at, '') = ''
          AND (
            COALESCE(result_message_id, '') != ''
            OR (
              require_reply = 0
              AND datetime(requested_at) <= datetime('now', ?)
            )
            OR (
              -- #20: a require_reply run that is stale AND has no active owner
              -- to ever produce the reply is orphaned — nothing will close it
              -- otherwise, so it lingers as a false "reply pending" forever.
              require_reply = 1
              AND datetime(requested_at) <= datetime('now', ?)
              AND NOT EXISTS (
                SELECT 1 FROM dispatch_runs r2
                WHERE r2.target_agent = dispatch_runs.target_agent
                  AND r2.id != dispatch_runs.id
                  AND r2.status IN ('queued', 'claimed', 'running')
              )
              AND NOT EXISTS (
                SELECT 1 FROM agent_sessions s
                WHERE s.agent_id = dispatch_runs.target_agent
                  AND s.status IN ('starting', 'running', 'recovering', 'restarting', 'cli-takeover')
              )
            )
            OR (
              -- Channel/resident wrapper bounces: claude-channel.js polls in
              -- a fresh wrapper after restart and has no memory of prior
              -- 'delivered' runs. Reconcile after a short window so the
              -- agent's working-status doesn't pin indefinitely.
              require_reply = 1
              AND execution_mode IN ('channel', 'resident')
              AND datetime(requested_at) <= datetime('now', ?)
            )
          )
        ORDER BY requested_at ASC
        LIMIT ?
        """,
        (
            f"-{max(1, int(stale_hours or 24))} hours",
            f"-{max(1, int(stale_hours or 24))} hours",
            f"-{max(1, int(channel_stale_minutes or 30))} minutes",
            limit,
        ),
    )
    rows = await cursor.fetchall()
    now = _now()
    closed: list[dict[str, str]] = []
    for row in rows:
        run_id = str(row["id"] or "").strip()
        if not run_id:
            continue
        has_result = bool(str(row["result_message_id"] or "").strip())
        needs_reply = bool(int((row["require_reply"] if "require_reply" in row.keys() else 0) or 0))
        if has_result:
            reason = "result_linked"
            summary = "Closed delivered run after result reply was linked."
        elif needs_reply:
            reason = "stale_delivery_orphaned_no_owner"
            summary = "Closed stale delivered run requiring a reply: no active owner remains to ever produce it."
        else:
            reason = "stale_delivery_no_reply_required"
            summary = "Closed stale delivered run that did not require a reply."
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'completed',
                summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ? AND status = 'delivered'
            """,
            (summary, now, run_id),
        )
        await _append_dispatch_event(db, run_id, "reconciled", summary)
        closed.append({"runId": run_id, "reason": reason})
    return closed


async def _prune_superseded_bridges(
    db,
    *,
    ttl_hours: int = 24,
    chunk: int = 2000,
    max_chunks: int = 50,
) -> int:
    """Reclaim superseded bridge_instances rows (holistic-review F4, 2026-05-31).

    Supersession sets `superseded_by` but nothing ever deleted the row, so the
    table grew monotonically with every wrapper relaunch (observed: 83/98 rows
    superseded). LIVE (non-superseded) rows are NEVER touched — only rows that
    have been superseded for longer than `ttl_hours` (keyed on superseded_at,
    falling back to last_seen). claim_bridge_id on dispatch_runs is a plain
    string (no FK), and any in-flight run owned by a superseded bridge was failed
    at supersession time, so deleting aged superseded rows orphans nothing.
    Chunked so a live control plane is never locked for long.
    """
    removed = 0
    for _ in range(max_chunks):
        cur = await db.execute(
            """
            DELETE FROM bridge_instances WHERE id IN (
                SELECT id FROM bridge_instances
                WHERE COALESCE(superseded_by, '') != ''
                  AND datetime(COALESCE(superseded_at, last_seen, '1970-01-01')) < datetime('now', ?)
                ORDER BY datetime(COALESCE(superseded_at, last_seen, '1970-01-01')) ASC
                LIMIT ?
            )
            """,
            (f"-{max(1, int(ttl_hours))} hours", int(chunk)),
        )
        await db.commit()
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        removed += n
        if n < chunk:
            break
    return removed


async def _prune_orphaned_dispatch_runs(
    db,
    *,
    ttl_hours: int = 24,
    chunk: int = 2000,
    max_chunks: int = 50,
) -> int:
    """Reclaim TERMINAL dispatch_runs whose endpoints have no live owner (WS4 Task 4.3).

    A tombstoned/removed agent leaves its dispatch_runs behind forever — the
    rows reference an agent that no longer exists, so they accrue with every
    test agent and every team teardown. This prunes only runs that are SAFE to
    drop, conservatively:

      DELETE a run iff ALL of:
        - status is TERMINAL ('completed', 'failed', 'cancelled') — never an
          in-flight 'queued'/'claimed'/'delivered'/'running' run; and
        - it is older than `ttl_hours` (keyed on finished_at, falling back to
          requested_at) — recent audit history of a just-removed agent is kept; and
        - NEITHER `target_agent` NOR `from_agent` is a CURRENTLY-LIVE agent
          (present in the `agents` table). A live agent is one still registered;
          a tombstoned/removed/unknown ref is not. This is the hard safety
          guarantee: a run touching ANY live agent is its history and is NEVER
          deleted.

    Endpoints like 'dashboard' or an external sender are 'unknown' (not in
    `agents`) and so do not protect a row — but a row is only pruned when BOTH
    ends lack a live owner, so no live agent ever loses inbound or outbound
    history. Chunked so a live control plane is never locked for long.
    """
    cutoff = f"-{max(1, int(ttl_hours))} hours"
    removed = 0
    for _ in range(max_chunks):
        cur = await db.execute(
            """
            DELETE FROM dispatch_runs WHERE id IN (
                SELECT id FROM dispatch_runs r
                WHERE r.status IN ('completed', 'failed', 'cancelled')
                  AND datetime(COALESCE(r.finished_at, r.requested_at)) < datetime('now', ?)
                  AND NOT EXISTS (SELECT 1 FROM agents a WHERE a.id = r.target_agent)
                  AND NOT EXISTS (SELECT 1 FROM agents a WHERE a.id = r.from_agent)
                ORDER BY datetime(COALESCE(finished_at, requested_at)) ASC
                LIMIT ?
            )
            """,
            (cutoff, int(chunk)),
        )
        await db.commit()
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        removed += n
        if n < chunk:
            break
    return removed


async def _prune_terminal_history(
    db,
    *,
    terminal_event_ttl_hours: int = 24,
    dispatch_event_ttl_hours: int = 72,
    ended_output_ttl_hours: int = 24,
    chunk: int = 5000,
    max_chunks: int = 200,
) -> dict[str, int]:
    """Bounded history retention so the DB does not grow forever.

    The live console scrollback is the (already 64KB-capped)
    terminal_sessions.output column — that is what the dashboard reads and is
    NOT touched for active sessions. This only trims redundant audit history:
    per-chunk terminal_events past a TTL, dispatch_events past a TTL, and the
    output blob of long-ended terminals. Chunked deletes keep each statement
    short so a live control plane is never locked for long.
    """
    counts = {"terminal_events": 0, "terminal_events_capped": 0, "dispatch_events": 0, "ended_output_cleared": 0}
    keep_events_per_terminal = 200

    async def _chunked_delete(sql: str, params: tuple) -> int:
        removed = 0
        for _ in range(max_chunks):
            cur = await db.execute(sql, params)
            await db.commit()
            n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            removed += n
            if n < chunk:
                break
        return removed

    counts["terminal_events"] = await _chunked_delete(
        f"DELETE FROM terminal_events WHERE id IN ("
        f"SELECT id FROM terminal_events WHERE created_at < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(terminal_event_ttl_hours))} hours",),
    )
    counts["dispatch_events"] = await _chunked_delete(
        f"DELETE FROM dispatch_events WHERE id IN ("
        f"SELECT id FROM dispatch_events WHERE created_at < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(dispatch_event_ttl_hours))} hours",),
    )
    # Per-terminal cap: chatty long-lived consoles produce hundreds of
    # thousands of event rows *within* the TTL window, so age alone cannot
    # bound them. Keep only the most recent N per terminal. Per-terminal
    # indexed deletes (idx_terminal_events_terminal on terminal_id,id) stay
    # fast and short even on a large table.
    term_ids = [
        r["terminal_id"]
        for r in await (await db.execute("SELECT DISTINCT terminal_id FROM terminal_events")).fetchall()
    ]
    for tid in term_ids:
        cutoff_row = await (await db.execute(
            "SELECT id FROM terminal_events WHERE terminal_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
            (tid, keep_events_per_terminal),
        )).fetchone()
        if not cutoff_row:
            continue
        cutoff_id = cutoff_row["id"]
        for _ in range(max_chunks):
            cur = await db.execute(
                f"DELETE FROM terminal_events WHERE id IN ("
                f"SELECT id FROM terminal_events WHERE terminal_id = ? AND id <= ? "
                f"ORDER BY id ASC LIMIT {int(chunk)})",
                (tid, cutoff_id),
            )
            await db.commit()
            n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            counts["terminal_events_capped"] += n
            if n < chunk:
                break

    cur = await db.execute(
        "UPDATE terminal_sessions SET output = '' "
        "WHERE status IN ('stopped', 'failed', 'ended', 'cancelled') "
        "AND COALESCE(output, '') != '' "
        "AND updated_at < datetime('now', ?)",
        (f"-{max(1, int(ended_output_ttl_hours))} hours",),
    )
    await db.commit()
    counts["ended_output_cleared"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return counts


@router.post("/dispatch/handoffs/repair")
async def repair_dispatch_handoffs(request: Request, limit: int = Query(100, ge=1, le=500)):
    db = await get_db()
    try:
        closed_delivered = await _close_reconcilable_delivered_runs(db, limit=limit)
        cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('completed', 'failed', 'cancelled')
              AND COALESCE(result_message_id, '') = ''
            ORDER BY requested_at ASC
            LIMIT ?
            """,
            (max(1, limit - len(closed_delivered)),),
        )
        rows = await cursor.fetchall()
        mirrored = []
        dashboard_reports = []
        skipped_delivery_only = 0
        skipped = 0
        for row in rows:
            if _is_delivery_only_claude_run(row):
                skipped_delivery_only += 1
                continue
            message_id = await _mirror_missing_dispatch_handoff(db, row)
            if message_id:
                mirrored.append({"runId": row["id"], "messageId": message_id})
            else:
                skipped += 1

        report_cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE require_reply = 0
              AND status = 'completed'
              AND COALESCE(summary, '') != ''
            ORDER BY requested_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        report_rows = await report_cursor.fetchall()
        for row in report_rows:
            message_id = await _maybe_report_async_manager_result_to_dashboard(db, row)
            if message_id:
                dashboard_reports.append({"runId": row["id"], "messageId": message_id})

        await db.commit()
        ws = await _get_ws(request)
        if ws and (mirrored or dashboard_reports or closed_delivered):
            await ws.broadcast(
                "dispatch_handoffs_repaired",
                {"mirrored": len(mirrored), "dashboardReports": len(dashboard_reports), "closedDelivered": len(closed_delivered)},
            )
        return {
            "ok": True,
            "mirrored": len(mirrored),
            "dashboardReports": len(dashboard_reports),
            "closedDelivered": len(closed_delivered),
            "skippedDeliveryOnly": skipped_delivery_only,
            "skipped": skipped,
            "runs": mirrored,
            "reports": dashboard_reports,
            "closed": closed_delivered,
        }
    finally:
        await db.close()


def _contract_list_query(
    *,
    where_sql: str = "",
    order_sql: str = "ORDER BY r.requested_at DESC",
    limit_sql: str = "LIMIT ?",
) -> str:
    return f"""
        SELECT
            r.*,
            m.source AS message_source,
            m.body AS message_body,
            m.timestamp AS message_timestamp,
            rr.read_at AS source_read_at,
            result.body AS result_body,
            result.timestamp AS result_timestamp,
            COALESCE(reminder.reminder_count, 0) AS reminder_count,
            COALESCE(reminder.last_reminder_at, '') AS last_reminder_at
        FROM dispatch_runs r
        LEFT JOIN messages m ON m.id = r.message_id
        LEFT JOIN read_receipts rr ON rr.message_id = r.message_id AND rr.agent_id = r.target_agent
        LEFT JOIN messages result ON result.id = r.result_message_id
        LEFT JOIN (
            SELECT run_id, COUNT(*) AS reminder_count, MAX(created_at) AS last_reminder_at
            FROM dispatch_events
            WHERE event_type = 'reply_reminder'
            GROUP BY run_id
        ) reminder ON reminder.run_id = r.id
        WHERE (
            r.require_reply = 1
            OR r.message_type IN ('request','review','error')
            OR (r.priority IN ('high','urgent') AND r.message_type NOT IN ('info','response','approval'))
        )
        {where_sql}
        {order_sql}
        {limit_sql}
    """


@router.get("/contracts")
async def list_work_contracts(
    request: Request,
    agentId: Optional[str] = None,
    fromAgent: Optional[str] = None,
    state: Optional[str] = Query(None, pattern="^(open|overdue|working|queued|seen|sent|missing_reply|failed|answered|closed)$"),
    category: Optional[str] = Query(None, pattern="^(direct|channel|self_wake)$"),
    includeClosed: bool = Query(False),
    limit: int = Query(120, ge=1, le=500),
):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        where = []
        params: list[Any] = []
        if agentId:
            where.append("AND r.target_agent = ?")
            params.append(agentId)
        if fromAgent:
            where.append("AND r.from_agent = ?")
            params.append(fromAgent)
        if category == "direct":
            where.append("AND r.from_agent != r.target_agent AND COALESCE(m.source, 'direct') != 'channel'")
        elif category == "channel":
            where.append("AND COALESCE(m.source, '') = 'channel'")
        elif category == "self_wake":
            where.append("AND r.from_agent = r.target_agent")
        stale_hours = max(1, int(settings.get("contract_stale_hours", 24) or 24))
        normalized_state = str(state or "").strip().lower()
        if normalized_state == "open":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('completed','failed','cancelled')")
        elif normalized_state == "answered":
            where.append("AND COALESCE(r.result_message_id, '') != ''")
        elif normalized_state == "closed":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status = 'completed' AND r.require_reply = 0")
        elif normalized_state == "missing_reply":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status = 'completed'")
        elif normalized_state == "failed":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status IN ('failed','cancelled')")
        elif normalized_state == "working":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status IN ('claimed','running')")
        elif normalized_state == "queued":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status = 'queued'")
        elif normalized_state == "overdue":
            reminder_minutes = max(1, int(settings.get("reply_reminder_minutes", DEFAULT_SETTINGS["reply_reminder_minutes"]) or DEFAULT_SETTINGS["reply_reminder_minutes"]))
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('completed','failed','cancelled') AND datetime(r.requested_at) <= datetime('now', ?)")
            params.append(f"-{reminder_minutes} minutes")
        elif normalized_state == "seen":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('queued','claimed','running','completed','failed','cancelled') AND COALESCE(rr.read_at, '') != ''")
        elif normalized_state == "sent":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('queued','claimed','running','completed','failed','cancelled') AND COALESCE(rr.read_at, '') = ''")

        closed_state_requested = normalized_state in {"answered", "closed", "missing_reply", "failed"}
        if includeClosed or closed_state_requested:
            where.append(
                """
                AND (
                    COALESCE(r.result_message_id, '') = ''
                    OR r.status IN ('queued','claimed','running')
                    OR datetime(COALESCE(r.finished_at, r.requested_at)) >= datetime('now', ?)
                )
                """
            )
            params.append(f"-{stale_hours} hours")
        else:
            where.append(
                """
                AND COALESCE(r.result_message_id, '') = ''
                AND r.status NOT IN ('completed','failed','cancelled')
                """
            )
        params.append(limit)
        cursor = await db.execute(_contract_list_query(where_sql="\n".join(where)), params)
        now_s = time.time()
        rows = [_contract_row_to_dict(row, settings=settings, now_s=now_s) for row in await cursor.fetchall()]
        if normalized_state == "open":
            rows = [row for row in rows if row["state"] in {"sent", "seen", "queued", "working", "overdue"}]
        elif normalized_state:
            rows = [row for row in rows if row["state"] == normalized_state]

        summary = {
            "total": len(rows),
            "open": sum(1 for row in rows if row["state"] in {"sent", "seen", "queued", "working", "overdue"}),
            "overdue": sum(1 for row in rows if row["overdue"]),
            "working": sum(1 for row in rows if row["state"] == "working"),
            "queued": sum(1 for row in rows if row["state"] == "queued"),
            "missingReply": sum(1 for row in rows if row["state"] == "missing_reply"),
            "answered": sum(1 for row in rows if row["state"] == "answered"),
            "selfWake": sum(1 for row in rows if row["category"] == "self_wake"),
            "channel": sum(1 for row in rows if row["category"] == "channel"),
        }
        return {"ok": True, "summary": summary, "contracts": rows, "settings": {
            "replyContractsEnabled": bool(settings.get("reply_contracts_enabled", True)),
            "replyReminderMinutes": int(settings.get("reply_reminder_minutes", DEFAULT_SETTINGS["reply_reminder_minutes"]) or DEFAULT_SETTINGS["reply_reminder_minutes"]),
            "replyReminderRepeatMinutes": int(settings.get("reply_reminder_repeat_minutes", DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]) or DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]),
            "replyReminderMaxCount": max(0, int(settings.get("reply_reminder_max_count", 0) or 0)),
            "contractStaleHours": int(settings.get("contract_stale_hours", 24) or 24),
        }}
    finally:
        await db.close()


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


def _contract_reminder_body(row) -> str:
    message_id = str(row["message_id"] or "").strip()
    target = str(row["target_agent"] or "").strip()
    sender = str(row["from_agent"] or "").strip()
    subject = str(row["subject"] or "").strip() or "(no subject)"
    read_hint = (
        f'comms_inbox(agentId="{target}", messageId="{message_id}")'
        if message_id
        else f'comms_run_status(runId="{row["id"]}")'
    )
    reply_hint = (
        f'comms_send(from="{target}", to="{sender}", type="response", inReplyTo="{message_id}", '
        f'subject="Re: {subject}", body="<answer, blocker, or result>")'
        if message_id and sender
        else f'comms_send(from="{target}", to="{sender or "original-sender"}", type="response", body="<answer, blocker, or result>")'
    )
    return (
        "Automated aify-comms reminder: this work message still needs an explicit reply.\n\n"
        f"Original sender: {sender}\n"
        f"Original subject: {subject}\n"
        f"Original message id: {message_id or '(run has no source message id)'}\n"
        f"Original run id: {row['id']}\n\n"
        "Read it if needed:\n"
        f"{read_hint}\n\n"
        "Use this exact reply anchor when closing the original contract:\n"
        f"{reply_hint}\n\n"
        "Close the contract by replying to the original sender/result, not by merely acknowledging this reminder. "
        "If you are blocked, reply with blocker, evidence checked, and next action."
    )


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
        body = _contract_reminder_body(row)
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
                "dashboard",
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
            from_agent="dashboard",
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


@router.post("/contracts/reminders/run")
async def run_contract_reminders(
    request: Request,
    runId: Optional[str] = None,
    dryRun: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    try:
        payload = await _run_contract_reminders_once(db, request=request, run_id=runId, dry_run=dryRun, limit=limit)
        await db.commit()
        return payload
    finally:
        await db.close()


@router.post("/contracts/hygiene/repair-read-receipts")
async def repair_contract_read_receipts(request: Request, limit: int = Query(500, ge=1, le=2000)):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE COALESCE(message_id, '') != ''
              AND status IN ('claimed','running','completed','failed','cancelled')
            ORDER BY requested_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        now = _now()
        repaired = 0
        for row in await cursor.fetchall():
            repaired += await _mark_dispatch_source_messages_read(db, row, row["target_agent"], now)
        await db.commit()
        ws = await _get_ws(request)
        if ws and repaired:
            await ws.broadcast("contract_read_receipts_repaired", {"count": repaired})
        return {"ok": True, "repaired": repaired}
    finally:
        await db.close()


@router.patch("/dispatch/runs/{run_id}")
async def update_dispatch_run(run_id: str, req: DispatchRunUpdate, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")

        updates = []
        params = []
        now = _now()

        if req.status:
            updates.append("status = ?")
            params.append(req.status)
            if req.status == "running" and not row["started_at"]:
                updates.append("started_at = ?")
                params.append(now)
            if req.status in _DISPATCH_TERMINAL_STATUSES:
                updates.append("finished_at = ?")
                params.append(now)
        if req.summary is not None:
            updates.append("summary = ?")
            params.append(req.summary)
        if req.error is not None:
            updates.append("error_text = ?")
            params.append(req.error)
        if req.resultMessageId is not None:
            normalized_result_message_id = str(req.resultMessageId or "").strip()
            if normalized_result_message_id or not str(row["result_message_id"] or "").strip():
                updates.append("result_message_id = ?")
                params.append(normalized_result_message_id)
        if req.externalThreadId is not None:
            updates.append("external_thread_id = ?")
            params.append(req.externalThreadId)
        if req.externalTurnId is not None:
            updates.append("external_turn_id = ?")
            params.append(req.externalTurnId)
        if req.runtime is not None:
            updates.append("runtime = ?")
            params.append(req.runtime)
        if req.requireReply is not None:
            updates.append("require_reply = ?")
            params.append(1 if req.requireReply else 0)

        if updates:
            params.append(run_id)
            await db.execute(f"UPDATE dispatch_runs SET {', '.join(updates)} WHERE id = ?", params)
            await _invalidate_agent_live_state(db, row["target_agent"])
            if req.status in ("completed", "failed", "cancelled"):
                await _fail_pending_controls_for_run(
                    db,
                    run_id,
                    handled_at=now,
                    response_text=f'Run ended with status "{req.status}" before the control could be handled.',
                )
                refreshed_cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                refreshed_row = await refreshed_cursor.fetchone()
                mirrored_message_id = await _mirror_missing_dispatch_handoff(db, refreshed_row)
                dashboard_message_id = await _mirror_dashboard_run_summary_to_chat(db, refreshed_row)
                result_message_id = str((refreshed_row["result_message_id"] if refreshed_row else "") or mirrored_message_id or dashboard_message_id or "").strip()
                await _close_steered_contracts_for_parent_run(
                    db,
                    refreshed_row,
                    result_message_id=result_message_id,
                )
                await _maybe_report_async_manager_result_to_dashboard(db, refreshed_row)
                if refreshed_row:
                    # Send-deadlock fix (2026-06-02): an rr=0 channel/resident
                    # delivery that the bridge just marked completed is NOT
                    # sustained work — clear the recipient's turn_busy (which the
                    # delivery re-pulse left stamped) so a queued send isn't held
                    # behind a phantom turn for up to 120s. rr=1 runs keep their
                    # turn_busy and clear via _mark_dispatch_run_answered when the
                    # reply lands; the guard ensures we never clear while another
                    # rr=1 turn is still open (anti-feedback-loop invariant).
                    if (
                        req.status == "completed"
                        and not _row_require_reply(refreshed_row)
                        and str((refreshed_row["execution_mode"] or "")).strip().lower() in {"channel", "resident"}
                    ):
                        await _clear_turn_busy_if_no_open_reply_owing_run(
                            db, refreshed_row["target_agent"], run_id
                        )
                    await _apply_pending_resident_takeover_if_ready(db, refreshed_row["target_agent"])
                    if req.status == "completed":
                        await _run_contract_reminders_once(
                            db,
                            request=request,
                            target_agent_id=refreshed_row["target_agent"],
                            limit=25,
                            recent_only=True,
                        )

        if req.agentStatus:
            await db.execute(
                "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
                (req.agentStatus, now, row["target_agent"])
            )
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (row["target_agent"],))).fetchone()
            await _touch_current_agent_session(
                db,
                row["target_agent"],
                _json_loads_or(agent_row["runtime_state"], {}) if agent_row else {},
                now,
            )

        if req.appendEvent:
            await _append_dispatch_event(db, run_id, req.eventType or "info", req.appendEvent)

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_updated", {"runId": run_id, "status": req.status or row["status"]})
        return {"ok": True, "runId": run_id}
    finally:
        await db.close()


@router.post("/dispatch/runs/{run_id}/control")
async def request_dispatch_control(run_id: str, req: DispatchControlRequest, request: Request):
    action = (req.action or "").strip().lower()
    if action not in {"interrupt", "steer"}:
        raise HTTPException(400, "Unsupported control action")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run:
            raise HTTPException(404, f"Run '{run_id}' not found")
        if run["status"] not in {"claimed", "running"}:
            raise HTTPException(409, f"Run '{run_id}' is not active")

        control_id = await _append_dispatch_control(
            db,
            run_id,
            from_agent=req.from_agent or "",
            action=action,
            body=req.body or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_requested", {"runId": run_id, "controlId": control_id, "action": action})
        return {"ok": True, "controlId": control_id, "runId": run_id, "action": action, "status": "pending"}
    finally:
        await db.close()


@router.post("/dispatch/controls/claim")
async def claim_dispatch_controls(req: DispatchControlClaimRequest, request: Request):
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        machine_id = req.machineId or ""
        if machine_id and agent["machine_id"] and not _machine_ids_same_host(agent["machine_id"], machine_id):
            await db.rollback()
            return {"ok": True, "controls": []}

        # Claim pending controls for this agent. No filter on run status —
        # Claude resident runs complete immediately on delivery, so their
        # controls would never be claimable under the old ('claimed','running')
        # filter. The channel bridge polls for controls independently and
        # delivers them as notifications regardless of run state.
        controls_cursor = await db.execute(
            """
            SELECT dc.*, dr.target_agent, dr.status as run_status
            FROM dispatch_controls dc
            JOIN dispatch_runs dr ON dr.id = dc.run_id
            WHERE dr.target_agent = ? AND dc.status = 'pending'
              AND (? = '' OR dc.run_id = ?)
            ORDER BY dc.requested_at ASC, dc.id ASC
            LIMIT 20
            """,
            (req.agentId, req.runId or "", req.runId or "")
        )
        controls = await controls_cursor.fetchall()
        if not controls:
            await db.commit()
            return {"ok": True, "controls": []}

        claimed_at = _now()
        results = []
        for control in controls:
            await db.execute(
                "UPDATE dispatch_controls SET status = 'claimed', claim_machine_id = ?, claimed_at = ? WHERE id = ?",
                (machine_id, claimed_at, control["id"])
            )
            results.append({
                "id": control["id"],
                "runId": control["run_id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "requestedAt": control["requested_at"],
                "claimedAt": claimed_at,
            })

        await db.commit()
        return {"ok": True, "controls": results}
    finally:
        await db.close()


@router.patch("/dispatch/controls/{control_id}")
async def update_dispatch_control(control_id: str, req: DispatchControlUpdate, request: Request):
    if req.status not in {"completed", "failed"}:
        raise HTTPException(400, "Unsupported control status")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
        control = await cursor.fetchone()
        if not control:
            raise HTTPException(404, f"Control '{control_id}' not found")

        handled_at = _now()
        await db.execute(
            "UPDATE dispatch_controls SET status = ?, response_text = ?, handled_at = ? WHERE id = ?",
            (req.status, req.response or "", handled_at, control_id)
        )
        if req.status == "completed" and (control["source_message_id"] or "").strip():
            run_cursor = await db.execute(
                "SELECT target_agent FROM dispatch_runs WHERE id = ?",
                (control["run_id"],),
            )
            run = await run_cursor.fetchone()
            if run and (run["target_agent"] or "").strip():
                msg_cursor = await db.execute(
                    "SELECT 1 FROM messages WHERE id = ?",
                    ((control["source_message_id"] or "").strip(),),
                )
                if await msg_cursor.fetchone():
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        ((control["source_message_id"] or "").strip(), run["target_agent"], handled_at),
                    )
        await _append_dispatch_event(
            db,
            control["run_id"],
            f"control:{control['action']}:{req.status}",
            req.response or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_updated", {"controlId": control_id, "status": req.status})
        return {"ok": True, "controlId": control_id, "status": req.status}
    finally:
        await db.close()


@router.delete("/messages/{message_id}")
async def unsend_message(message_id: str, request: Request):
    """Delete a message by ID. Also removes associated read receipts."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        message_ids = [message_id]
        if (row["source"] or "") == "channel" and not (row["to_agent"] or ""):
            fanout_cursor = await db.execute(
                "SELECT id FROM messages WHERE id LIKE ? AND channel = ? AND source = 'channel'",
                (f"{message_id}-%", row["channel"] or ""),
            )
            message_ids.extend([fanout["id"] for fanout in await fanout_cursor.fetchall()])
        cancelled_dispatch_run_ids = await _cancel_queued_dispatch_runs_for_message_ids(db, message_ids)
        deleted = await _delete_messages_by_ids(db, message_ids)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_deleted", {"id": message_id, "deleted": deleted})
            for run_id in cancelled_dispatch_run_ids:
                await ws.broadcast("dispatch_updated", {"runId": run_id, "status": "cancelled"})
        return {
            "ok": True,
            "id": message_id,
            "deleted": deleted,
            "cancelledDispatchRuns": len(cancelled_dispatch_run_ids),
            "cancelledDispatchRunIds": cancelled_dispatch_run_ids,
        }
    finally:
        await db.close()


@router.post("/messages/{message_id}/read")
async def set_message_read_state(message_id: str, request: Request):
    body = await request.json()
    agent_id = str(body.get("agentId") or "").strip()
    read = bool(body.get("read", True))
    if not agent_id:
        raise HTTPException(400, "Need agentId")
    validate_name(agent_id, "agent ID")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, to_agent FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        if row["to_agent"] != agent_id:
            raise HTTPException(403, f'Message "{message_id}" is not addressed to "{agent_id}"')

        if read:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (message_id, agent_id, _now()),
            )
        else:
            await db.execute(
                "DELETE FROM read_receipts WHERE message_id = ? AND agent_id = ?",
                (message_id, agent_id),
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_read_state", {"id": message_id, "agentId": agent_id, "read": read})
        return {"ok": True, "id": message_id, "agentId": agent_id, "read": read}
    finally:
        await db.close()


@router.post("/messages/conversation/clear")
async def clear_direct_conversation(req: ConversationClearRequest, request: Request):
    agent_id = str(req.agentId or "").strip()
    peer_id = str(req.peerId or "").strip()
    if not agent_id or not peer_id:
        raise HTTPException(400, "Need agentId and peerId")
    validate_name(agent_id, "agent ID")
    validate_name(peer_id, "peer agent ID")

    db = await get_db()
    try:
        deleted = await _delete_messages_where(
            db,
            """
            source = 'direct'
            AND channel IS NULL
            AND (
                (from_agent = ? AND to_agent = ?)
                OR (from_agent = ? AND to_agent = ?)
            )
            """,
            (agent_id, peer_id, peer_id, agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("conversation_cleared", {"agentId": agent_id, "peerId": peer_id, "deleted": deleted})
        return {"ok": True, "agentId": agent_id, "peerId": peer_id, "deleted": deleted}
    finally:
        await db.close()


@router.post("/messages/cleanup/orphan-unread")
async def cleanup_orphan_unread_messages(request: Request):
    """Delete unread inbox messages addressed to removed agents."""
    db = await get_db()
    try:
        deleted = await _delete_messages_where(
            db,
            """
            id IN (
                SELECT m.id
                FROM messages m
                LEFT JOIN agents a ON a.id = m.to_agent
                LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = m.to_agent
                WHERE m.to_agent IS NOT NULL AND a.id IS NULL AND r.message_id IS NULL
            )
            """,
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws and deleted:
            await ws.broadcast("messages_cleaned", {"kind": "orphan_unread", "deleted": deleted})
        return {"ok": True, "deleted": deleted}
    finally:
        await db.close()


# ─── Shared Artifacts ────────────────────────────────────────────────────────

@router.get("/shared")
async def list_shared(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM shared_artifacts ORDER BY shared_at DESC")
        files = []
        for row in await cursor.fetchall():
            files.append({
                "name": row["name"], "from": row["from_agent"],
                "description": row["description"], "size": row["size"],
                "sharedAt": row["shared_at"],
            })
        return {"files": files}
    finally:
        await db.close()


@router.post("/shared")
async def share_artifact(
    request: Request,
    from_agent: str = Form(...), name: str = Form(...),
    description: str = Form(""), content: str = Form(None),
    file: UploadFile = File(None),
):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        now = _now()
        size = 0
        is_binary = False
        if file:
            shared_dir = _shared_dir(request)
            file_path = shared_dir / name
            data = await file.read()
            size = len(data)
            is_binary = True
            file_path.write_bytes(data)
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, file_path, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, str(file_path), size, 1, now)
            )
        else:
            text = content or ""
            size = len(text)
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, content, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, text, size, 0, now)
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("file_shared", {"name": name, "from": from_agent})
        return {"ok": True, "name": name, "size": size, "isBinary": is_binary}
    finally:
        await db.close()


@router.get("/shared/{name}")
async def read_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM shared_artifacts WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Artifact '{name}' not found")
        meta = {"from": row["from_agent"], "description": row["description"], "size": row["size"], "sharedAt": row["shared_at"]}
        if row["is_binary"] and row["file_path"]:
            from fastapi.responses import FileResponse
            return FileResponse(row["file_path"], filename=name)
        return {"content": row["content"], "meta": meta}
    finally:
        await db.close()


@router.delete("/shared/{name}")
async def delete_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        # Delete file if binary
        cursor = await db.execute("SELECT file_path FROM shared_artifacts WHERE name = ? AND is_binary = 1", (name,))
        row = await cursor.fetchone()
        if row and row["file_path"]:
            p = Path(row["file_path"])
            if p.exists(): p.unlink()
        await db.execute("DELETE FROM shared_artifacts WHERE name = ?", (name,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# ─── Channels ────────────────────────────────────────────────────────────────

@router.get("/channels")
async def list_channels(request: Request, agentId: Optional[str] = None):
    viewer_id = str(agentId or "").strip()
    if viewer_id:
        validate_name(viewer_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels")
        channels = []
        for ch in await cursor.fetchall():
            mc = await db.execute("SELECT COUNT(*) FROM channel_members WHERE channel_name = ?", (ch["name"],))
            member_count = (await mc.fetchone())[0]
            history_where, history_params = _normalize_channel_history_where(ch["name"])
            msg_c = await db.execute(f"SELECT COUNT(*) FROM messages WHERE {history_where}", history_params)
            msg_count = (await msg_c.fetchone())[0]
            unread_count = 0
            if viewer_id:
                unread_c = await db.execute(
                    """
                    SELECT COUNT(*)
                    FROM messages m
                    LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = ?
                    WHERE m.channel = ? AND m.to_agent = ? AND m.source = 'channel' AND r.message_id IS NULL
                    """,
                    (viewer_id, ch["name"], viewer_id),
                )
                unread_count = (await unread_c.fetchone())[0]
            channels.append({
                "name": ch["name"], "description": ch["description"],
                "createdBy": ch["created_by"], "createdAt": ch["created_at"],
                "members": [], "memberCount": member_count, "messageCount": msg_count,
                "unreadCount": unread_count,
            })
            # Fetch member list
            mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (ch["name"],))
            channels[-1]["members"] = [r["agent_id"] for r in await mem_c.fetchall()]
        return {"channels": channels}
    finally:
        await db.close()


@router.post("/channels")
async def create_channel(req: ChannelCreate, request: Request):
    validate_name(req.name, "channel name")
    db = await get_db()
    try:
        now = _now()
        try:
            await db.execute(
                "INSERT INTO channels (name, description, created_by, created_at) VALUES (?,?,?,?)",
                (req.name, req.description or "", req.createdBy, now)
            )
        except Exception:
            raise HTTPException(409, f"Channel '{req.name}' already exists")
        await db.execute(
            "INSERT INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
            (req.name, req.createdBy, now)
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("channel_created", {"name": req.name})
        return {"ok": True, "channel": req.name}
    finally:
        await db.close()


@router.get("/channels/{name}")
async def get_channel(
    name: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = 0,
    agentId: Optional[str] = None,
):
    validate_name(name, "channel name")
    viewer_id = str(agentId or "").strip()
    if viewer_id:
        validate_name(viewer_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        ch = await cursor.fetchone()
        if not ch:
            raise HTTPException(404, f"Channel '{name}' not found")

        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]

        history_where, history_params = _normalize_channel_history_where(name)
        total_c = await db.execute(f"SELECT COUNT(*) FROM messages WHERE {history_where}", history_params)
        total = (await total_c.fetchone())[0]

        # Paginate newest first
        msg_c = await db.execute(
            f"SELECT * FROM messages WHERE {history_where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            history_params + (limit, offset)
        )
        messages = []
        for row in await msg_c.fetchall():
            read = True
            fanout_id = ""
            if viewer_id and row["from_agent"] != viewer_id and row["from_agent"] != "_system":
                fanout_id = _channel_fanout_message_id(row["id"], viewer_id)
                read_cursor = await db.execute(
                    "SELECT 1 FROM read_receipts WHERE message_id = ? AND agent_id = ?",
                    (fanout_id, viewer_id),
                )
                read = bool(await read_cursor.fetchone())
            messages.append({
                "id": row["id"], "from": row["from_agent"], "type": row["type"],
                "body": row["body"], "priority": row["priority"], "timestamp": row["timestamp"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                "read": read,
                "fanoutMessageId": fanout_id,
            })
        # Reverse so oldest is first in the returned slice (chat order)
        messages.reverse()

        return {
            "name": ch["name"], "description": ch["description"],
            "members": members, "totalMessages": total, "messages": messages,
        }
    finally:
        await db.close()


@router.delete("/channels/{name}")
async def delete_channel(name: str, request: Request):
    db = await get_db()
    try:
        await db.execute("DELETE FROM channel_members WHERE channel_name = ?", (name,))
        await _delete_messages_where(db, "channel = ?", (name,))
        cursor = await db.execute("DELETE FROM channels WHERE name = ?", (name,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Channel '{name}' not found")
        return {"ok": True}
    finally:
        await db.close()


@router.post("/channels/{name}/join")
async def join_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        if not await cursor.fetchone():
            raise HTTPException(404, f"Channel '{name}' not found")
        now = _now()
        insert_cursor = await db.execute(
            "INSERT OR IGNORE INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
            (name, req.agentId, now)
        )
        changed = insert_cursor.rowcount > 0
        if changed:
            await db.execute(
                "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} joined the channel", int(time.time()*1000))
            )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        ws = await _get_ws(request)
        if ws and changed:
            await ws.broadcast("channel_membership", {"channel": name, "agentId": req.agentId, "action": "join", "members": members})
        return {"ok": True, "members": members, "changed": changed}
    finally:
        await db.close()


@router.post("/channels/{name}/leave")
async def leave_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        if not await cursor.fetchone():
            raise HTTPException(404, f"Channel '{name}' not found")
        delete_cursor = await db.execute("DELETE FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.agentId))
        changed = delete_cursor.rowcount > 0
        if changed:
            await db.execute(
                "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} left the channel", int(time.time()*1000))
            )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        ws = await _get_ws(request)
        if ws and changed:
            await ws.broadcast("channel_membership", {"channel": name, "agentId": req.agentId, "action": "leave", "members": members})
        return {"ok": True, "members": members, "changed": changed}
    finally:
        await db.close()


@router.post("/channels/{name}/read")
async def mark_channel_read(name: str, request: Request):
    validate_name(name, "channel name")
    body = await request.json()
    agent_id = str(body.get("agentId") or "").strip()
    if not agent_id:
        raise HTTPException(400, "Need agentId")
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        member_cursor = await db.execute(
            "SELECT 1 FROM channel_members WHERE channel_name = ? AND agent_id = ?",
            (name, agent_id),
        )
        if not await member_cursor.fetchone():
            raise HTTPException(403, f'Agent "{agent_id}" is not a member of #{name}')
        now = _now()
        cursor = await db.execute(
            """
            SELECT id
            FROM messages
            WHERE channel = ? AND to_agent = ? AND source = 'channel'
            """,
            (name, agent_id),
        )
        rows = await cursor.fetchall()
        for row in rows:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (row["id"], agent_id, now),
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("channel_read", {"channel": name, "agentId": agent_id, "count": len(rows)})
        return {"ok": True, "channel": name, "agentId": agent_id, "read": len(rows)}
    finally:
        await db.close()


@router.post("/channels/{name}/send")
async def send_channel_message(name: str, req: ChannelMessage, request: Request):
    validate_name(name, "channel name")
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)

        # Verify membership
        cursor = await db.execute("SELECT 1 FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.from_agent))
        if not await cursor.fetchone():
            raise HTTPException(403, f"Agent '{req.from_agent}' is not a member of #{name}. Join first.")

        msg_id = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)
        subject = f"#{name}: {req.body[:80]}"
        should_trigger = False if req.silent else req.trigger is not False

        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        recipients = []
        inbox_message_ids = {}
        suppressed_duplicates = []
        for member in members:
            if member == req.from_agent:
                continue
            if await _has_recent_direct_delivery_for_channel_fanout(
                db,
                from_agent=req.from_agent,
                recipient_id=member,
                message_type=req.type,
                body=req.body,
                timestamp_ms=ts,
            ):
                suppressed_duplicates.append(member)
                continue
            recipient_msg_id = f"{msg_id}-{member}"
            recipients.append(member)
            inbox_message_ids[member] = recipient_msg_id

        launchable_recipients = []
        not_started = []
        dispatch_recipients = [recipient_id for recipient_id in recipients if recipient_id != "dashboard"]
        if should_trigger and recipients:
            prefer_steer = (req.steer is not False) and not bool(req.queueIfBusy)
            allow_queue_busy = bool(req.queueIfBusy) or prefer_steer
            launchable_recipients, not_started = await _preflight_live_send_recipients(
                db,
                dispatch_recipients,
                allow_steer=prefer_steer,
                allow_queue_busy=allow_queue_busy,
            )
            if not_started:
                recipient_info = {}
                for recipient_id in recipients:
                    info = await _get_recipient_info(db, recipient_id)
                    if info:
                        recipient_info[recipient_id] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Channel message was not sent because one or more recipients cannot start live work now.",
                    "members": members,
                    "recipients": recipients,
                    "suppressedDuplicates": suppressed_duplicates,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                }

        # Channel message (canonical)
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (msg_id, req.from_agent, name, "channel", req.type, subject, req.body, req.priority or "normal", 1 if should_trigger else 0, ts)
        )

        # Deliver to each member's inbox (except sender)
        for member in members:
            if member != req.from_agent:
                recipient_msg_id = inbox_message_ids.get(member)
                if not recipient_msg_id:
                    continue
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        recipient_msg_id, req.from_agent, member, name, "channel", req.type, subject,
                        req.body, req.priority or "normal", 1 if should_trigger and member != "dashboard" else 0, ts
                    )
                )

        dispatch_runs = []
        if should_trigger and dispatch_recipients:
            dispatch_runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=subject,
                body=req.body,
                priority=req.priority or "normal",
                in_reply_to=None,
                dispatch_mode="start_if_possible",
                execution_mode="managed",
                requested_runtime=None,
                message_id=inbox_message_ids.get(recipients[0]) if len(recipients) == 1 else None,
                source_message_ids=inbox_message_ids,
                steer=prefer_steer,
                require_reply=False,
            )
            dispatch_runs = await _finalize_dispatch_runs(db, dispatch_runs, launchable_recipients, not_started)

        recipient_info = {}
        for recipient_id in recipients:
            info = await _get_recipient_info(db, recipient_id)
            if info:
                recipient_info[recipient_id] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("channel_message", {"channel": name, "from": req.from_agent, "body": req.body[:200]})
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "new_message", {"from": req.from_agent, "subject": subject, "channel": name})
            for run in dispatch_runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
        # Wake up any listening members
        for member in members:
            if member != req.from_agent:
                _wake_agent(member)
        return {
            "ok": True,
            "messageId": msg_id,
            "members": members,
            "recipients": recipients,
            "suppressedDuplicates": suppressed_duplicates,
            "recipientStatus": recipient_info,
            "dispatchRuns": dispatch_runs,
            "notStarted": not_started,
        }
    finally:
        await db.close()


# ─── Settings ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM settings")
        saved = {}
        for row in await cursor.fetchall():
            try:
                saved[row["key"]] = json.loads(row["value"])
            except Exception:
                saved[row["key"]] = row["value"]
        return {**DEFAULT_SETTINGS, **saved}
    finally:
        await db.close()


@router.put("/settings")
async def update_settings(request: Request):
    body = await request.json()
    db = await get_db()
    try:
        for key, value in body.items():
            if key in DEFAULT_SETTINGS:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                    (key, json.dumps(value))
                )
        settings = await _load_settings(db)
        if any(str(key).startswith("managed_") for key in body.keys()):
            await _apply_managed_runtime_defaults(db, settings)
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("settings_updated")
        return await get_settings(request)
    finally:
        await db.close()


# ─── Stats ───────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(request: Request):
    db = await get_db()
    try:
        repaired_active_runs = await _repair_unusable_active_runs(db)
        if repaired_active_runs:
            await db.commit()
        agents_c = await db.execute("SELECT COUNT(*) FROM agents")
        agents = (await agents_c.fetchone())[0]

        environments_c = await db.execute("SELECT COUNT(*) FROM environments WHERE status != 'forgotten'")
        environments = (await environments_c.fetchone())[0]

        spawn_c = await db.execute("SELECT status, COUNT(*) as cnt FROM spawn_requests GROUP BY status")
        spawn_by_status = {row["status"]: row["cnt"] for row in await spawn_c.fetchall()}

        sessions_c = await db.execute("SELECT COUNT(*) FROM agent_sessions WHERE status IN ('starting','running')")
        active_sessions = (await sessions_c.fetchone())[0]

        total_c = await db.execute("SELECT COUNT(*) FROM messages WHERE source = 'direct'")
        total = (await total_c.fetchone())[0]

        # Unread direct inbox messages for currently registered agents only
        unread_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN agents a ON a.id = m.to_agent
            LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
            WHERE m.to_agent IS NOT NULL AND m.source = 'direct' AND r.message_id IS NULL
            """
        )
        unread = (await unread_c.fetchone())[0]

        channel_unread_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN agents a ON a.id = m.to_agent
            LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
            WHERE m.to_agent IS NOT NULL AND m.source = 'channel' AND r.message_id IS NULL
            """
        )
        channel_unread = (await channel_unread_c.fetchone())[0]

        orphan_unread_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            LEFT JOIN agents a ON a.id = m.to_agent
            LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
            WHERE m.to_agent IS NOT NULL AND a.id IS NULL AND r.message_id IS NULL
            """
        )
        orphan_unread = (await orphan_unread_c.fetchone())[0]

        # Today
        today_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")) * 1000)
        today_c = await db.execute("SELECT COUNT(*) FROM messages WHERE timestamp >= ?", (today_start,))
        today = (await today_c.fetchone())[0]
        since_24h_ms = int((time.time() - 24 * 60 * 60) * 1000)
        since_24h_iso = _iso_from_ms(since_24h_ms)
        direct_24h_c = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE source = 'direct' AND timestamp >= ?",
            (since_24h_ms,),
        )
        direct_24h = (await direct_24h_c.fetchone())[0]
        channel_24h_c = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE source = 'channel' AND to_agent IS NULL AND timestamp >= ?",
            (since_24h_ms,),
        )
        channel_24h = (await channel_24h_c.fetchone())[0]
        active_pairs_c = await db.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT
                    CASE WHEN from_agent < to_agent THEN from_agent ELSE to_agent END AS a,
                    CASE WHEN from_agent < to_agent THEN to_agent ELSE from_agent END AS b
                FROM messages
                WHERE source = 'direct'
                  AND to_agent IS NOT NULL
                  AND timestamp >= ?
                GROUP BY a, b
            )
            """,
            (since_24h_ms,),
        )
        active_pairs_24h = (await active_pairs_c.fetchone())[0]
        run_failures_24h_c = await db.execute(
            "SELECT COUNT(*) FROM dispatch_runs WHERE status IN ('failed','cancelled') AND COALESCE(finished_at, requested_at) >= ?",
            (since_24h_iso,),
        )
        run_failures_24h = (await run_failures_24h_c.fetchone())[0]
        failed_spawns_24h_c = await db.execute(
            "SELECT COUNT(*) FROM spawn_requests WHERE status = 'failed' AND updated_at >= ?",
            (since_24h_iso,),
        )
        failed_spawns_24h = (await failed_spawns_24h_c.fetchone())[0]
        completed_runs_24h_c = await db.execute(
            "SELECT COUNT(*) FROM dispatch_runs WHERE status = 'completed' AND COALESCE(finished_at, requested_at) >= ?",
            (since_24h_iso,),
        )
        completed_runs_24h = (await completed_runs_24h_c.fetchone())[0]

        # By type
        type_c = await db.execute("SELECT type, COUNT(*) as cnt FROM messages WHERE source = 'direct' GROUP BY type")
        by_type = {row["type"]: row["cnt"] for row in await type_c.fetchall()}

        # By agent
        agent_c = await db.execute("SELECT to_agent, COUNT(*) as cnt FROM messages WHERE to_agent IS NOT NULL GROUP BY to_agent")
        by_agent = {row["to_agent"]: row["cnt"] for row in await agent_c.fetchall()}

        # Shared
        shared_c = await db.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(size),0) as total_size FROM shared_artifacts")
        shared_row = await shared_c.fetchone()

        dispatch_c = await db.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM dispatch_runs
            GROUP BY status
            """
        )
        dispatch_by_status = {row["status"]: row["cnt"] for row in await dispatch_c.fetchall()}
        reply_pending_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('completed', 'failed', 'cancelled')
              AND COALESCE(result_message_id, '') = ''
              AND NOT (
                  runtime = 'claude-code'
                  AND status = 'completed'
                  AND COALESCE(summary, '') LIKE 'Delivered to Claude resident session%'
              )
            """
        )
        reply_pending = (await reply_pending_c.fetchone())[0]

        return {
            "agents": agents,
            "environments": environments,
            "spawn_requests_total": sum(spawn_by_status.values()),
            "spawn_requests_by_status": spawn_by_status,
            "active_sessions": active_sessions,
            "total_messages": total,
            "unread_messages": unread,
            "channel_unread_messages": channel_unread,
            "orphan_unread_messages": orphan_unread,
            "messages_today": today,
            "direct_messages_24h": direct_24h,
            "channel_posts_24h": channel_24h,
            "active_dm_pairs_24h": active_pairs_24h,
            "run_failures_24h": run_failures_24h,
            "failed_spawns_24h": failed_spawns_24h,
            "completed_runs_24h": completed_runs_24h,
            "messages_by_type": by_type,
            "messages_by_agent": by_agent,
            "shared_files": shared_row["cnt"],
            "shared_size_bytes": shared_row["total_size"],
            "shared_size_mb": round(shared_row["total_size"] / 1048576, 2),
            "dispatch_runs_total": sum(dispatch_by_status.values()),
            "dispatch_runs_by_status": dispatch_by_status,
            "dispatch_reply_pending": reply_pending,
        }
    finally:
        await db.close()


@router.get("/analytics")
async def get_analytics(request: Request, analytics_range: str = Query("hour", alias="range", pattern="^(hour|day|month|all)$")):
    selected_range = analytics_range
    db = await get_db()
    try:
        settings = await _load_settings(db)
        now_s = int(time.time())
        message_where = """
          (
            (source = 'direct' AND to_agent IS NOT NULL)
            OR (source = 'channel' AND to_agent IS NULL)
          )
        """

        async def count_messages_between(start_ms: int, end_ms: int) -> int:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM messages WHERE {message_where} AND timestamp >= ? AND timestamp < ?",
                (start_ms, end_ms),
            )
            return int((await cursor.fetchone())[0])

        hourly = []
        hour_start = (now_s // 3600) * 3600
        for i in range(23, -1, -1):
            start_s = hour_start - i * 3600
            hourly.append({
                "label": time.strftime("%H:00", time.localtime(start_s)),
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, (start_s + 3600) * 1000),
            })

        daily = []
        today_struct = time.localtime(now_s)
        today_start_s = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d", today_struct), "%Y-%m-%d")))
        for i in range(29, -1, -1):
            start_s = today_start_s - i * 86400
            daily.append({
                "label": time.strftime("%m-%d", time.localtime(start_s)),
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, (start_s + 86400) * 1000),
            })

        monthly = []
        year = today_struct.tm_year
        month = today_struct.tm_mon
        for i in range(11, -1, -1):
            m = month - i
            y = year
            while m <= 0:
                m += 12
                y -= 1
            next_m = m + 1
            next_y = y
            if next_m > 12:
                next_m = 1
                next_y += 1
            start_s = int(time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1)))
            end_s = int(time.mktime((next_y, next_m, 1, 0, 0, 0, 0, 0, -1)))
            monthly.append({
                "label": f"{y}-{m:02d}",
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, end_s * 1000),
            })

        all_time_c = await db.execute(
            f"""
            SELECT strftime('%Y-%m', datetime(timestamp / 1000, 'unixepoch')) AS bucket,
                   MIN(timestamp) AS start_ms,
                   COUNT(*) AS cnt
            FROM messages
            WHERE {message_where}
            GROUP BY bucket
            ORDER BY bucket ASC
            """
        )
        all_time = [
            {"label": row["bucket"] or "unknown", "start": _iso_from_ms(int(row["start_ms"] or 0)), "count": int(row["cnt"] or 0)}
            for row in await all_time_c.fetchall()
        ]

        since_s_by_range = {
            "hour": now_s - 24 * 3600,
            "day": now_s - 30 * 86400,
            "month": now_s - 366 * 86400,
        }
        since_s = since_s_by_range.get(selected_range)
        run_where = ""
        run_params: tuple[Any, ...] = ()
        spawn_where = ""
        spawn_params: tuple[Any, ...] = ()
        message_count_where = message_where
        message_count_params: tuple[Any, ...] = ()
        if since_s is not None:
            since_iso = _iso_from_ms(since_s * 1000)
            since_ms = since_s * 1000
            run_where = "WHERE COALESCE(finished_at, requested_at) >= ?"
            run_params = (since_iso,)
            spawn_where = "WHERE updated_at >= ?"
            spawn_params = (since_iso,)
            message_count_where = f"{message_where} AND timestamp >= ?"
            message_count_params = (since_ms,)

        status_c = await db.execute(
            f"SELECT status, COUNT(*) as cnt FROM dispatch_runs {run_where} GROUP BY status",
            run_params,
        )
        runs_by_status = {row["status"]: row["cnt"] for row in await status_c.fetchall()}
        message_total_c = await db.execute(
            f"SELECT COUNT(*) FROM messages WHERE {message_count_where}",
            message_count_params,
        )
        message_total = int((await message_total_c.fetchone())[0])
        spawn_status_c = await db.execute(
            f"SELECT status, COUNT(*) as cnt FROM spawn_requests {spawn_where} GROUP BY status",
            spawn_params,
        )
        spawns_by_status = {row["status"]: row["cnt"] for row in await spawn_status_c.fetchall()}

        agents_c = await db.execute("SELECT * FROM agents")
        agent_rows = await agents_c.fetchall()
        live_agents = 0
        online_agents = 0
        working_agents = 0
        for row in agent_rows:
            mode = _agent_wake_mode(row)
            if mode != "message-only" and mode != "disabled":
                live_agents += 1
            status = await _compute_agent_status(row, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30), db)
            if not status.startswith("offline") and not status.startswith("stale"):
                online_agents += 1
            if status.startswith("working"):
                working_agents += 1

        env_c = await db.execute("SELECT COUNT(*) FROM environments WHERE status = 'online'")
        online_environments = int((await env_c.fetchone())[0])

        return {
            "ok": True,
            "messagesPerHour": hourly,
            "messagesPerDay": daily,
            "messagesPerMonth": monthly,
            "messagesPerAllTime": all_time,
            "range": selected_range,
            "rangeLabel": {"hour": "last 24 hours", "day": "last 30 days", "month": "last 12 months", "all": "all time"}[selected_range],
            "messageTotal": message_total,
            "runsByStatus": runs_by_status,
            "runTotal": sum(runs_by_status.values()),
            "spawnRequestsByStatus": spawns_by_status,
            "spawnRequestTotal": sum(spawns_by_status.values()),
            "liveAgents": live_agents,
            "onlineAgents": online_agents,
            "workingAgents": working_agents,
            "onlineEnvironments": online_environments,
        }
    finally:
        await db.close()


# ─── Clear ───────────────────────────────────────────────────────────────────

@router.post("/clear")
async def clear_data(req: ClearRequest, request: Request):
    db = await get_db()
    try:
        cutoff = None
        if req.olderThanHours:
            cutoff = int((time.time() - req.olderThanHours * 3600) * 1000)

        deleted_messages = 0
        deleted_files = 0
        deleted_agents = 0

        if req.target in ("inbox", "all"):
            if req.agentId:
                if cutoff:
                    deleted_messages += await _delete_messages_where(
                        db,
                        "to_agent = ? AND timestamp < ?",
                        (req.agentId, cutoff),
                    )
                else:
                    deleted_messages += await _delete_messages_where(db, "to_agent = ?", (req.agentId,))
            else:
                if cutoff:
                    deleted_messages += await _delete_messages_where(
                        db,
                        "to_agent IS NOT NULL AND timestamp < ?",
                        (cutoff,),
                    )
                else:
                    deleted_messages += await _delete_messages_where(db, "to_agent IS NOT NULL")

        if req.target in ("shared", "all"):
            # Delete binary files from disk
            cursor = await db.execute("SELECT file_path FROM shared_artifacts WHERE is_binary = 1")
            for row in await cursor.fetchall():
                if row["file_path"]:
                    p = Path(row["file_path"])
                    if p.exists(): p.unlink()
            count_cursor = await db.execute("SELECT COUNT(*) FROM shared_artifacts")
            deleted_files = (await count_cursor.fetchone())[0]
            await db.execute("DELETE FROM shared_artifacts")

        if req.target in ("agents", "all"):
            if req.agentId and req.target == "agents":
                agent_rows = await (await db.execute("SELECT id FROM agents WHERE id = ?", (req.agentId,))).fetchall()
            else:
                agent_rows = await (await db.execute("SELECT id FROM agents")).fetchall()
            agent_ids = [row["id"] for row in agent_rows]
            for agent_id in agent_ids:
                deleted_agents += await _remove_agent_record(
                    db,
                    agent_id,
                    removed_by="clear",
                    reason=f'clear(target="{req.target}")',
                )

        if req.target in ("channels", "all"):
            await db.execute("DELETE FROM channel_members")
            deleted_messages += await _delete_messages_where(db, "channel IS NOT NULL")
            await db.execute("DELETE FROM channels")

        if req.target == "all":
            await db.execute("DELETE FROM read_receipts")
            await db.execute("DELETE FROM agent_sessions")
            await db.execute("DELETE FROM spawn_requests")
            await db.execute("DELETE FROM spawn_specs")
            await db.execute("DELETE FROM environments")

        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("data_cleared", {"target": req.target})
        return {
            "ok": True,
            "deletedMessages": deleted_messages,
            "cleared": {
                "messages": deleted_messages,
                "files": deleted_files,
                "agents": deleted_agents,
            },
        }
    finally:
        await db.close()


# ─── Rotate ──────────────────────────────────────────────────────────────────

@router.post("/rotate")
async def rotate(request: Request):
    settings = await get_settings(request)
    if not settings.get("rotation_enabled", True):
        return {"ok": False, "reason": "Rotation disabled"}

    db = await get_db()
    try:
        stats = {"expired_messages": 0, "trimmed_messages": 0, "expired_files": 0, "stale_agents": 0}

        # Expire old messages
        retention_ms = int(settings["retention_days"] * 86400 * 1000)
        cutoff = int(time.time() * 1000) - retention_ms
        stats["expired_messages"] = await _delete_messages_where(db, "timestamp < ?", (cutoff,))

        # Trim per-agent inboxes
        max_msgs = settings["max_messages_per_agent"]
        agents_c = await db.execute("SELECT id FROM agents")
        for agent in await agents_c.fetchall():
            aid = agent["id"]
            c = await db.execute("SELECT COUNT(*) FROM messages WHERE to_agent = ?", (aid,))
            count = (await c.fetchone())[0]
            if count > max_msgs:
                trim = count - max_msgs
                stats["trimmed_messages"] += await _delete_messages_where(
                    db,
                    """
                    id IN (
                        SELECT id FROM messages
                        WHERE to_agent = ?
                        ORDER BY timestamp ASC
                        LIMIT ?
                    )
                    """,
                    (aid, trim),
                )

        # Mark stale agents
        stale_hours = settings["stale_agent_hours"]
        cursor = await db.execute(
            "UPDATE agents SET status = 'stale' WHERE status != 'stale' AND datetime(last_seen) < datetime('now', ? || ' hours')",
            (f"-{stale_hours}",)
        )
        stats["stale_agents"] = cursor.rowcount

        # Clean orphaned read receipts
        await db.execute("DELETE FROM read_receipts WHERE message_id NOT IN (SELECT id FROM messages)")

        await db.commit()
        return {"ok": True, "stats": stats}
    finally:
        await db.close()


# ─── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent.parent / "dashboard.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@router.get("/dashboard/dispatches", response_class=HTMLResponse)
async def dashboard_dispatches():
    return await dashboard()


@router.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse(Path(__file__).parent.parent / "favicon.svg", media_type="image/svg+xml")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return FileResponse(Path(__file__).parent.parent / "favicon.svg", media_type="image/svg+xml")
