"""The pre-split `update_dispatch_run`, frozen.

Not imported by anything. It is the ONE true original that
`test_update_dispatch_run_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/dispatch_messages/dispatch.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


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
        current_status = str(row["status"] or "").strip().lower()
        requested_status = str(req.status or "").strip().lower()
        effective_status = req.status
        if current_status in _DISPATCH_TERMINAL_STATUSES and requested_status != current_status:
            effective_status = None

        if effective_status:
            updates.append("status = ?")
            params.append(effective_status)
            if effective_status == "running" and not row["started_at"]:
                updates.append("started_at = ?")
                params.append(now)
            if effective_status in _DISPATCH_TERMINAL_STATUSES:
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
                        effective_status == "completed"
                        and not _row_require_reply(refreshed_row)
                        and str((refreshed_row["execution_mode"] or "")).strip().lower() in {"channel", "resident"}
                    ):
                        await _clear_turn_busy_if_no_open_reply_owing_run(
                            db, refreshed_row["target_agent"], run_id
                        )
                    await _apply_pending_resident_takeover_if_ready(db, refreshed_row["target_agent"])
                    if effective_status == "completed":
                        await _run_contract_reminders_once(
                            db,
                            request=request,
                            target_agent_id=refreshed_row["target_agent"],
                            limit=25,
                            recent_only=True,
                        )

        # MC1 (2026-06-06): only persist a status that is in the 8-status vocabulary.
        # Delivery PATCHes historically sent a non-vocab agentStatus:"active", which got
        # written raw into agents.status and leaked to the dashboard as a 9th status. Now an
        # out-of-vocab value is ignored (status is DERIVED from turn/liveness signals anyway);
        # only an explicit valid operator/runtime status is written. last_seen still refreshes.
        if req.agentStatus and req.agentStatus in VALID_STATUSES:
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
            await ws.broadcast("dispatch_updated", {"runId": run_id, "status": effective_status or row["status"]})
        return {"ok": True, "runId": run_id}
    finally:
        await db.close()
