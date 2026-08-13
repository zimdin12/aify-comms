"""The agent status DECISION: facts in, effective status out.

v0.5.4. The heart of the status engine, extracted from `service/control_plane.py` so its branches can be
tested directly. It had no direct test before this move — every existing status test reached it through a
database and a route, which is why a 147-line decision with eighteen conditions had no branch coverage.

The derivation in the carrier is three phases: gather facts (database reads), DECIDE (this module),
adjust the result (refresh windows, overrides). Only the middle phase is here.

A LEAF: imports two api_core siblings and nothing else. It does not import the control plane; the control
plane is now a caller.
"""

from __future__ import annotations

from typing import Any, Optional

from service.api_core.managed_env import _managed_console_is_booting
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES


async def _decide_effective_status(
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
):
    """Decide an agent's effective status from already-gathered facts. THE status derivation.

    v0.5.4, extracted verbatim out of the 551-line `_compute_live_status_cache`. This is the block that
    actually decides — everything before it in the carrier gathers facts from the database, everything
    after adjusts the result. Twelve assignment sites across four outcomes (offline, blocked, working,
    online) behind eighteen conditions.

    WHY IT MOVED: it was reachable only through a database and a route, so nothing tested its branches
    directly. Isolating it is the prerequisite for branch characterization; the reshape into a facts
    object comes after those tests exist, not before.

    NOT PURE, DELIBERATELY. One `await` remains — `_managed_console_is_booting` on a late branch. Hoisting
    it would make this a pure function of plain values and trivially testable, and it would also add a
    database query to EVERY status computation on a hot path. Keeping it async preserves the original
    call pattern exactly; purity is a later question, not a silent trade.

    ALL THREE OUTPUTS ARE ALSO PARAMETERS, and that is not redundancy. `reason` and `awaiting_reply` are
    initialized by the caller and read inside (`if not reason:`). `effective_status` is assigned by EARLIER
    branches in the caller and read here before some paths reassign it — the extract-method gate's live-in
    check caught that when it was omitted, which is precisely the NameError-after-split class it exists for.

    DB ACCESS: `db` is passed in and used for one read. No connection opened, no commit, no rollback.
    """
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
    return effective_status, reason, awaiting_reply
