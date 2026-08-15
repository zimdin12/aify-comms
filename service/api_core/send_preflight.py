"""Which recipients can actually start live work right now, and why the rest cannot.

RELOCATED from `service/api_core/dispatch_runs.py` in v0.5.4, byte-identical. It was 151 of that
module's 460 lines, calls no sibling there and reads none of its constants — which is what makes this
a relocation rather than a split.

IT IS A PREFLIGHT, NOT A CREATOR, and that is why it left. `dispatch_runs.py` is about writing and
finalising dispatch_runs rows; this decides, per recipient, whether a run is worth creating at all.
The three modules that call it — the direct send, the channel fan-out and the dispatch sweeps — all
want the answer BEFORE anything is written.

WHAT IT PRODUCES IS A PAIR: the recipients that can be launched, and `not_started`, the ones that
cannot with a reason attached. The reason is the point — a send that silently drops an unreachable
recipient looks identical to one that worked, which is the failure mode the `notStarted` payload and
`_dispatch_fix_hint` exist to prevent.

DB ACCESS: `db` is passed in, nothing opens a connection or commits — this joins its caller's
transaction.
"""
from __future__ import annotations

from typing import Any

from service.api_core.active_run_discard import _discard_unusable_active_run
from service.api_core.capabilities import _row_capabilities
from service.api_core.dispatch_hint import _dispatch_fix_hint
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.execution_mode import (
    _agent_execution_mode,
    _auto_return_resident_to_managed_if_possible,
)
from service.api_core.liveness import _resident_bridge_is_fresh
from service.api_core.managed_env import _managed_environment_unavailable_reason
from service.api_core.records import _status_with_dispatch
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.status_refresh import _compute_agent_status
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES


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
