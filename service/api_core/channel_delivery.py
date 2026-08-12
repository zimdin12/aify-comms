"""Channel delivery: which runtimes use it, whether one is eligible, and whether a worker is live.

The first LAYER-1 slice of the v0.5.4 decomposition, and the method had to change to reach it.

Every layer-1 function is blocked by some layer-0 helper the reviewer ruled too small and scattered
to own a module. That ruling was right about moving those helpers ALONE — a five-line predicate in a
`misc` module is the junk drawer this work exists to delete. It does not apply to moving one WITH its
consumer: `_channel_flag_enabled` is five lines and belongs beside the two functions that ask it a
question. So this slice moves the transitive CLOSURE of a subject seed rather than a single layer.

FOUR RUNTIME SETS, together because they are four answers to nearly the same question and have
drifted before:

    _CHANNEL_MANAGED_RUNTIMES          may a managed agent be woken over the channel
    _CHANNEL_CLAIM_RUNTIMES            may its bridge CLAIM a run          (route vs claim!)
    _CHANNEL_FLAG_GATED_RUNTIMES       is that gated behind a settings flag
    _CHANNEL_SIDECAR_DELIVERY_RUNTIMES is delivery done by a standalone sidecar

The route/claim split is not pedantry. Plan 4 set the server route for wrapper-backed runtimes while
the claim whitelist still held only claude-code, so bridges for codex/hermes/pi never requested the
mode and the server would have rejected them anyway — a route added without a claim added. Four sets
in one file is how the next reader sees they are four different questions.

`_CHANNEL_CLAIM_RUNTIMES` had NO reader left in the carrier: three modules reached it through
accessors and nothing else. A constant whose every consumer is a borrow shim does not have an owner,
it has a hiding place.

DB ACCESS: `db` passed in, no connection opened, no commit, no rollback.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from service.api_core.liveness import (
    _has_live_channel_sidecar,
    _has_live_managed_wrapper_child,
    _has_live_terminal_session,
)
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.clock import iso_to_epoch as _iso_to_epoch


_CHANNEL_MANAGED_RUNTIMES = {"claude-code"}
_CHANNEL_FLAG_GATED_RUNTIMES = {"hermes"}
_CHANNEL_CLAIM_RUNTIMES = _CHANNEL_MANAGED_RUNTIMES | {"codex", "hermes"}
_CHANNEL_SIDECAR_DELIVERY_RUNTIMES = {"claude-code", "hermes"}


class _WorkerLiveness(NamedTuple):
    """Result of the managed/resident live-worker probe.

    `has_live_worker` is the single boolean both the legacy derivation
    (_compute_live_status_cache) and the event engine (_gather_status_inputs)
    consume. The two reason flags are status-F1 / hermes-sidecar diagnostics the
    legacy reason text uses; they are NOT part of the engine's input contract.
    """
    has_live_worker: bool
    channel_managed_no_sidecar: bool
    channel_managed_no_console: bool


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


async def _worker_liveness_for(
    db, agent_row, *, agent_session_mode: str, live_session: bool
) -> _WorkerLiveness:
    """Decide whether an agent currently has a LIVE serving worker.

    This is the SINGLE definition of "has_live_worker" — extracted verbatim from
    _compute_live_status_cache (status-F1 console+sidecar rule, the channel-flag
    hermes channel-sidecar rule, and the FIX B3 codex managed-wrapper-child rule,
    plus the live_session terminal-row probe and resident fallback). Both the
    legacy derivation and _gather_status_inputs call it so old/new always agree on
    worker liveness. Behavior-preserving: callers pass the same `live_session` and
    `agent_session_mode` the original computed inline.
    """
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
    elif (
        not has_live_worker
        and agent_session_mode == "managed"
        and runtime_for_delivery == "codex"
    ):
        # FIX B3 (2026-06-03): a managed CODEX run hosted by a wrapper-backed
        # worker proves liveness with a fresh, non-superseded
        # `managed-wrapper-child` bridge heartbeat (the visible console's
        # in-session aify-comms MCP) — mirroring the hermes channel-sidecar gate
        # above. Without this, a transient app-server close (now no longer
        # instant-fatal per FIX B1) could fail the terminal rows → has_live_worker
        # stays False → the agent flips to `available` mid-work even while its
        # console is live and heartbeating. The wrapper-child heartbeat is the
        # real deliverability proof, so honor it here.
        if await _has_live_managed_wrapper_child(db, agent_row["id"]):
            has_live_worker = True
    return _WorkerLiveness(has_live_worker, channel_managed_no_sidecar, channel_managed_no_console)
