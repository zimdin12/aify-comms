"""Settle a terminal dispatch run without changing the agent's turn state.

Re-read the run after the update so handoffs and dashboards report the terminal row,
not its previous state. aify-env owns turn reporting.
"""
from __future__ import annotations

from fastapi import Request

from service.api_core.active_run_discard import _fail_pending_controls_for_run
from service.api_core.dashboard_run_report import (
    _maybe_report_async_manager_result_to_dashboard,
    _mirror_dashboard_run_summary_to_chat,
)
from service.api_core.dispatch_sweeps import (
    _mirror_missing_dispatch_handoff,
    _run_contract_reminders_once,
)
from service.reconcilers.dispatch_lifecycle import _close_steered_contracts_for_parent_run


async def _apply_pending_resident_takeover_if_ready(db, agent_id: str) -> bool:
    # Manual ownership model: a resident CLI registration must not take over a
    # managed identity at a turn boundary. Operators use /session-mode.
    return False


async def _settle_terminated_dispatch_run(db, run_id: str, effective_status,
                                          now: str, request: Request, *, operator_closed: bool = False) -> None:
            """Act only on terminal runs. An operator close is not an agent reply."""
            if effective_status in ("completed", "failed", "cancelled"):
                await _fail_pending_controls_for_run(
                    db,
                    run_id,
                    handled_at=now,
                    response_text=f'Run ended with status "{effective_status}" before the control could be handled.',
                )
                refreshed_cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                refreshed_row = await refreshed_cursor.fetchone()
                mirrored_message_id = await _mirror_missing_dispatch_handoff(db, refreshed_row)
                dashboard_message_id = await _mirror_dashboard_run_summary_to_chat(db, refreshed_row, operator_closed=operator_closed)
                result_message_id = str((refreshed_row["result_message_id"] if refreshed_row else "") or mirrored_message_id or dashboard_message_id or "").strip()
                await _close_steered_contracts_for_parent_run(
                    db,
                    refreshed_row,
                    result_message_id=result_message_id,
                )
                await _maybe_report_async_manager_result_to_dashboard(db, refreshed_row, operator_closed=operator_closed)
                if refreshed_row:
                    await _apply_pending_resident_takeover_if_ready(db, refreshed_row["target_agent"])
                    if effective_status == "completed":
                        await _run_contract_reminders_once(
                            db,
                            request=request,
                            target_agent_id=refreshed_row["target_agent"],
                            limit=25,
                            recent_only=True,
                        )
