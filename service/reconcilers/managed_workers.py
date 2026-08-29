"""Managed-worker hygiene and active-run repair — the last mapped slice of the v0.5 extraction.

v0.5 slice 10, extracted from `service/routers/api_v2.py`.

`_reconcile_managed_worker_hygiene` is 240 lines, the largest single body moved in this release and
well over the reviewer's ≥200 bound, so it anchors this slice rather than riding with a group.
`_repair_unusable_active_runs` (29 lines) came with it because it is the sweep's first step and reads
the same dispatch-state surface.

BORROWED, on measured caller count as in every slice since 4: nine functions and two constants, each
read through exactly one owner. This is the largest borrow set in the release, and it is the honest
measurement of how entangled managed-worker hygiene is with the router's liveness helpers —
`_has_live_channel_sidecar`, `_has_live_managed_wrapper_child` and `_has_live_terminal_session` are
the same family slice 3b deferred `_agent_liveness` over.

THAT IS THE DECISION THIS RELEASE ENDS ON. Ten slices moved ~3,800 lines out of the router, and what
remains is a leaf-module layer that still reaches back for one liveness family and a handful of
append/normalize helpers. Consolidating those is a real slice of its own — the one the router's own
TODO has been asking for since before this release started.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL
from service.api_core.dispatch_run_state import _mark_dispatch_run_answered
from service.api_core.reply_contract import _message_satisfies_reply_contract
from service.api_core.serialization import _json_loads_or  # v0.5.1c: the leaf owner, not via the router
from service.api_core.runtime import _normalize_runtime  # v0.5.1e: the leaf owner, not via the router
from service.api_core.events import _append_terminal_event  # v0.5.1i: the leaf owner
from service.api_core.events import _append_dispatch_event  # v0.5.1i: the leaf owner
from service.api_core.live_process_probes import (
    _has_live_channel_sidecar,
    _has_live_managed_wrapper_child,
    _has_live_terminal_session,
)
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
# Sibling reconcilers from slice 6 — a direct import, not a shim: both are leaf modules, so this is
# the extraction's first module-to-module edge that does not touch the router.
from service.reconcilers.terminal_runs import (
    _close_idle_claude_terminal_run_without_reply,
    _close_idle_pi_terminal_run_without_reply,
)
from service.api_core.channel_delivery import _CHANNEL_SIDECAR_DELIVERY_RUNTIMES
from service.api_core.active_run_discard import _discard_unusable_active_run
from service.api_core.tuning import MANAGED_ORPHAN_GRACE_SECONDS

logger = logging.getLogger(__name__)
















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
    result = {
        "managed_ghost_rows_reaped": 0,
        # NEWLY recorded orphans. The counter used to mean "matched this sweep", which is a
        # different thing entirely once the reap cannot clear its own predicate -- see below.
        "orphan_workers_reaped": 0,
        # Still in the orphan state from an earlier sweep. Reported rather than silently skipped:
        # an orphan nobody collected is exactly what an operator needs to see, and it is what the
        # old counter was really showing every minute without saying so.
        "orphan_workers_still_orphaned": 0,
    }
    # `.format()`, NOT an f-string: this query already interpolated `{placeholders}` that way, and
    # prefixing it with `f` made Python resolve that name at literal-evaluation time -- a NameError
    # on every call, which four tests caught immediately. The status fragment goes through the same
    # `.format()` rather than a second mechanism.
    cursor = await db.execute(
        """
        SELECT t.id AS terminal_id, t.agent_id AS agent_id, a.runtime AS runtime,
               t.updated_at AS updated_at
        FROM terminal_sessions t
        JOIN agents a ON a.id = t.agent_id
        WHERE a.session_mode = 'managed'
          AND a.runtime IN ({placeholders})
          AND t.status IN {terminal_active}
          AND t.id NOT LIKE 'vterm_%'
        """.format(
            placeholders=",".join("?" for _ in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES),
            terminal_active=TERMINAL_LIVE_FILTER_SQL,
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
        # DETERMINISTIC BOOT-RACE GUARD (2026-06-04): the claimer bridges
        # (claude-channel.js sidecar / managed-wrapper-child MCP) only register
        # AFTER claude finishes init — which includes SessionStart hooks that can
        # run for MINUTES (observed: a 1m28s one-time plugin dep-install). During
        # that boot the PTY is alive and STREAMING (the hook progress spinner),
        # but no claimer bridge exists yet, so the sidecar check alone cannot tell
        # "booting" from "dead" and would reap a live worker mid-boot (→ launches-
        # then-dies, agent stuck `available`). Gate the reap on PROCESS-liveness
        # signals instead of the lagging claimer — both EVENT-driven, not a fixed
        # boot timer:
        #   (a) a live managed-wrapper-child (the in-session aify MCP), or
        #   (b) fresh terminal output activity — `updated_at` is bumped by every
        #       bridge output frame (see _append_terminal_output), so a streaming
        #       PTY (booting or running) is provably alive.
        # Only when BOTH are absent (no claimer, no wrapper-child, no recent
        # output) is the worker genuinely dead and the console a ghost.
        if await _has_live_managed_wrapper_child(db, agent_id):
            continue
        term_updated = _iso_to_epoch(str(row["updated_at"] or "")) if "updated_at" in row.keys() else 0
        if term_updated and (
            datetime.now(timezone.utc).timestamp() - term_updated
        ) <= MANAGED_ORPHAN_GRACE_SECONDS:
            # The bridge is still streaming this PTY (e.g. booting through a long
            # SessionStart hook) → alive, not a ghost. Leave it.
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
        # ALREADY RECORDED? Then this sweep found the SAME orphan again, and the expensive half must
        # not run twice.
        #
        # NOTHING THIS FUNCTION DOES CAN MAKE ITS OWN PREDICATE FALSE. It clears a console pointer,
        # appends an event and invalidates a cache; the terminal stays `stopped`, the sidecar keeps
        # beating, and the actual process kill is host-side. So a genuine orphan re-matched on every
        # 60-second sweep, forever. Measured on the operator's live database: 1,664
        # `reconciled_managed_orphan_worker` events across 70 terminals -- one of them 200 events over
        # 3h22m, and two current ones at 91 each. That is 12.1% of all 13,699 terminal_events rows,
        # generated by a reaper that should fire once per orphan, and then pruned again by
        # `pruned_terminal_events_capped` in the same sweep.
        #
        # The `terminal_id` lookup rides `idx_terminal_events_terminal(terminal_id, id)`, and there
        # are only ever a handful of candidates per sweep.
        seen_before = await (
            await db.execute(
                "SELECT 1 FROM terminal_events WHERE terminal_id = ? AND event_type = ? LIMIT 1",
                (terminal_id, "reconciled_managed_orphan_worker"),
            )
        ).fetchone()
        if seen_before:
            # The status truth was already written and the cache already invalidated; re-doing either
            # changes nothing and costs a write plus a cold status recompute every minute.
            result["orphan_workers_still_orphaned"] += 1
            continue
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
