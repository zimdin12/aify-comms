"""Which queued dispatch run may this bridge actually take, and why the others were skipped.

Extracted from `_claim_dispatch_once` (`service/dispatch_claim.py`) in v0.5.4, byte-identical apart
from nothing — the body sits at its original 8-space column. That function was 422 lines, the second
largest in the repo, and this loop is the part with a decision in it: everything else around it is
the claim transaction.

WHY IT IS A LOOP AND NOT A FILTER. Each skip is RECORDED, not just dropped — a run the bridge cannot
execute gets a `skipped` dispatch event explaining which capability was missing. That is the
difference between an agent that looks idle for no reason and one whose run history says why, and it
is why the block writes to the database rather than returning a predicate.

`continue` and `break` travel WITH the loop they target, so they are not escapes: the extract-method
gate refuses a `break` whose loop stays behind in the caller, which is the shape that changes
behaviour. Here the loop moves too.

DB ACCESS: `db` is passed in, the only write is the skip event, and nothing opens a connection,
commits, or rolls back — this joins its caller's transaction, which is the condition for moving a
DB-touching helper out of `dispatch_claim.py`.
"""
from __future__ import annotations

from service.api_core.events import _append_dispatch_event
from service.api_core.execution_mode import _agent_execution_mode
from service.api_core.runtime import _normalize_runtime
from service.clock import now as _now


async def _select_claimable_run(
    db, req, runs, agent,
    agent_runtime, claim_settings, hold_explicit_queue, supported_modes,
):
        """The first run this bridge can execute, or None — recording a reason for each it skips."""
        selected_run = None
        for run in runs:
            if hold_explicit_queue and (
                bool(run["queue_if_busy"]) or not bool(run["steer_if_busy"])
            ):
                continue
            run_execution_mode = (run["execution_mode"] or "managed").strip().lower()
            if supported_modes and run_execution_mode not in supported_modes:
                continue
            # NORMALISED, like `execution_mode` three lines above. Raw, a `Message_Only` run was
            # not recognised here and started a turn the sender asked to be message-only.
            run_dispatch_mode = str(run["dispatch_mode"] or "").strip().lower()
            if run_dispatch_mode == "message_only":
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
                final_status = "failed" if run_dispatch_mode == "require_start" else "cancelled"
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason or "active dispatch unavailable", _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason or "active dispatch unavailable")
                continue
            if (run["execution_mode"] or execution_mode) != execution_mode:
                final_status = "failed" if run_dispatch_mode == "require_start" else "cancelled"
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
        return selected_run
