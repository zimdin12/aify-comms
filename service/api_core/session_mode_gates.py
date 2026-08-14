"""Gates a session-mode switch has to pass.

Extracted from `service/routers/agents/session_mode.py` in v0.5.4, whose `switch_agent_session_mode`
was 414 lines — the largest single route handler left in the tree.
"""
from __future__ import annotations

from fastapi import HTTPException

from service.api_core.active_run_lookup import _get_blocking_active_run


async def _enforce_switch_not_blocked_by_active_run(db, req, agent_id: str, new_mode: str, runtime: str,
                                                    switch_warnings) -> None:
        """Refuse a mode switch that would interrupt live work — unless the operator forced it.

        Extracted from `switch_agent_session_mode` in v0.5.4;
        `test_switch_agent_session_mode_split_is_inert.py` inlines it back and AST-compares against the
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
