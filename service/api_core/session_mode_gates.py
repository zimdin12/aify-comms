"""Gates a session-mode switch has to pass.

Extracted from `service/routers/agents/session_mode.py` in v0.5.4, whose `switch_agent_session_mode`
was 414 lines — the largest single route handler left in the tree.
"""
from __future__ import annotations

from fastapi import HTTPException

from service.api_core.agent_sessions import ENDED_AGENT_SESSION_STATUS_SQL
from service.api_core.managed_pty_for_dispatch import _ensure_managed_pty_for_dispatch
from service.api_core.capabilities import _managed_via_wrapper_for_runtime
from service.api_core.dispatch_start import (
    _coldstart_spawn_request_for_dispatch,
)
from service.api_core.dispatch_text import _coldstart_refusal_message
from service.api_core.agent_terminal_ops import _request_stop_agent_terminals
from service.api_core.active_run_lookup import _get_blocking_active_run


async def _enforce_switch_not_blocked_by_active_run(db, req, agent_id: str, new_mode: str, runtime: str,
                                                    switch_warnings) -> None:
        """Refuse a mode switch that would interrupt live work — unless the operator forced it.

        Extracted from `switch_agent_session_mode` in v0.5.4;
        `test_switch_agent_session_mode_split_is_inert.py` (retired in v0.6.13) inlined it back and AST-compared against the
        pre-split fixture. Body left at its original 8-space column so the multi-line SQL literal inside
        is preserved byte-for-byte — the gate compares ASTs and refuses a re-indent that rewrites a query.

        TWO CHECKS, AND ONLY THE FIRST REFUSES. An active dispatch run is a hard 409: flipping the mode
        under a running turn is how work gets lost. A missing managed backing is NOT — that used to 409
        too, and it stranded resident agents on offline machines, because since lazy auto-start a managed
        agent with no live backing is simply `available` and cold-starts on the next send. It appends a
        warning instead, which is why `switch_warnings` is passed in rather than returned: it is a list
        the caller reports either way.
        """
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
                    f"""
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                      AND status NOT IN {ENDED_AGENT_SESSION_STATUS_SQL}
                    ORDER BY last_seen DESC
                    LIMIT 1
                    """,
                    (agent_id, runtime),
                )).fetchone()
                if not managed_session:
                    # RELAXED (2026-06-11): this used to 409, but since lazy auto-start a
                    # managed agent with no live backing is simply `available` — it cold-starts
                    # on the next send and resolves its environment at claim time. Blocking the
                    # flip stranded resident agents on offline machines (operator-reported: an
                    # old resident session on another PC could not be switched). Allow the
                    # switch and surface a warning instead.
                    switch_warnings.append(
                        "No live managed backing yet — the agent reads `available` and a managed "
                        "worker will cold-start on the next send once its environment is online."
                    )


async def _start_managed_backing_after_switch(db, agent_id: str, new_mode: str, runtime: str, settings,
                                              requested_by: str, now: str, logger, side_effects) -> None:
        """Give a just-switched agent something to run on, without disturbing what is already there.

        Extracted from `switch_agent_session_mode` in v0.5.4;
        `test_switch_agent_session_mode_split_is_inert.py` (retired in v0.6.13) inlined it back and AST-compared against the
        pre-split fixture. Body left at its original 8-space column so the literals inside are preserved
        byte-for-byte.

        THE WRAPPER-BACKED RUNTIMES TAKE THE OTHER PATH, and that carve-out is the whole reason this
        block is not two lines long. Eager-starting codex/hermes through
        `_ensure_managed_pty_for_dispatch` re-attaches a PTY to the leftover RESIDENT agent_sessions row
        — a `*-aify --resume`, not a managed-warm worker — so no `managed-wrapper-child` bridge
        registers and the next channel run is rejected `managed_wrapper_child_required` and queues
        forever. They retire the leftover row and cold-start a spawn request instead, so a bridge spawns
        a real managed worker whose in-session MCP registers the claimer.

        EVERYTHING HERE IS BEST-EFFORT. The mode switch has already been committed by the time this
        runs; failing to start a backing must not fail the switch, so the outcome is reported through
        `side_effects` rather than raised. `side_effects` is a dict the caller reports either way, which
        is why it is passed in rather than returned.
        """
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
                    _switch_coldstart_warnings: list[str] = []
                    coldstarted = await _coldstart_spawn_request_for_dispatch(
                        db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by,
                        warnings=_switch_coldstart_warnings,
                    )
                    if _switch_coldstart_warnings:
                        side_effects["handleCollisionWarnings"] = _switch_coldstart_warnings
                    if coldstarted:
                        side_effects["managedSpawnRequested"] = True
                    else:
                        side_effects["error"] = _coldstart_refusal_message(
                            _switch_coldstart_warnings, runtime)
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
                # managed -> resident: stop any active managed PTY.
                #
                # THE CONTROL IS WHAT STOPS A PROCESS; the row only records that it was asked. This
                # wrote `stopping` and nothing else, so an operator switching a RUNNING managed agent
                # to resident left its worker running in its Herdr pane, unaddressed by anything: the
                # host reaps an orphan only after ten minutes of silence, and an attended pane is
                # never silent that long. Meanwhile the row sat `stopping`, which the status engine
                # renders as `working`, so the dashboard showed a busy agent that was nobody's.
                #
                # `stop_agent_worker` already knew this and says so in its own comment -- "Database
                # state cannot stop a process on the owning host. Queue the bridge-side stop before
                # marking the row stopped" -- and this path simply never called it. Asked by the
                # operator, 2026-09-21.
                #
                # EVERY live terminal, through the helper operator Stop uses: this read one
                # (`_active_terminal_for_agent`, newest session only), so an agent holding two live
                # PTYs kept one running after the switch.
                stopped = await _request_stop_agent_terminals(
                    db, agent_id, requested_by=requested_by, now=now,
                )
                if stopped:
                    side_effects["stoppedTerminalIds"] = stopped
        except Exception as exc:  # pragma: no cover — surface, do not abort
            logger.warning("session-mode side-effect failed for %s: %s", agent_id, exc)
            side_effects["error"] = str(exc)
