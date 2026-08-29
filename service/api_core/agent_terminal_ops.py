"""Two operations on an agent's terminals: asking them all to stop, and finding the live console one.

96 lines, and the pair is here because both answer "which of this agent's terminals matters right now"
and then act on the answer — one by requesting a stop for every live terminal, the other by picking the
single one the console should attach to. Neither is a status question (api_core/terminal_status.py owns
the vocabulary), neither writes output (api_core/terminal_output.py), and neither decides ownership
(api_core/terminal_ownership.py). Four terminal leaves now, each answering a different question, which is
deliberate: a single `terminal.py` would mean "anything to do with terminals".

`_REAP_TRIAD_BODY_SENTINEL` came with `_request_stop_agent_terminals` because it had ZERO code readers
in the carrier — one accessor and one test — which is hiding-place evidence rather than ownership. The
stop request is the only thing that writes it.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback.
"""

from __future__ import annotations


from service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL, TERMINAL_STOPPABLE_STATUS_SQL
from service.api_core.events import _append_terminal_control
from service.api_core.serialization import _json_loads_or
from service.api_core.terminal_status import _TERMINAL_END_STATUSES


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
        f"""
        SELECT id, environment_id, bridge_id, session_id FROM terminal_sessions
        WHERE agent_id = ?
          AND id NOT LIKE 'vterm_%'
          AND status IN {TERMINAL_STOPPABLE_STATUS_SQL}
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


async def _resolve_live_console_terminal(db, agent_id: str):
    """Resolve an agent's LIVE console terminal row.

    Prefers the terminal_sessions row pointed at by runtime_state.consoleTerminal.
    terminalId (managed claude) or runtime_state.virtualTerminalId (pi/hermes
    virtual). If that pointer is unset or points at an ended terminal, FALL BACK to
    the agent's newest genuinely-live PTY terminal (2026-06-17): the consoleTerminal
    pointer is only written on a register-with-console path, so a managed console that
    LAZY-STARTS on a message leaves it empty — console_tail/console_input then wrongly
    reported "no live console" while the dashboard (which resolves via the live terminal
    row) showed it. The fallback makes the MCP tools agree with the dashboard. Returns
    None only when the agent truly has no live console. Agent-scoped on purpose: callers
    can only reach a terminal *through* the agent, never by arbitrary id; the fallback
    only ever returns a LIVE row that belongs to this agent (no stale/foreign extras).
    """
    agent_row = await (
        await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))
    ).fetchone()
    if not agent_row:
        return None
    runtime_state = _json_loads_or(agent_row["runtime_state"], {})
    terminal_id = ""
    if isinstance(runtime_state, dict):
        console_terminal = runtime_state.get("consoleTerminal")
        if isinstance(console_terminal, dict):
            terminal_id = str(console_terminal.get("terminalId") or "").strip()
        if not terminal_id:
            terminal_id = str(runtime_state.get("virtualTerminalId") or "").strip()
    if terminal_id:
        terminal = await (
            await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ? AND agent_id = ?",
                (terminal_id, agent_id),
            )
        ).fetchone()
        if terminal and str(terminal["status"] or "").strip().lower() not in _TERMINAL_END_STATUSES:
            return terminal
    # Fallback: the agent's newest LIVE, non-virtual PTY terminal (the same live-terminal
    # source the dashboard renders), for lazy-started managed consoles whose pointer is unset.
    return await (
        await db.execute(
            "SELECT * FROM terminal_sessions WHERE agent_id = ? "
            f"AND status IN {TERMINAL_LIVE_FILTER_SQL} "
            "AND id NOT LIKE 'vterm_%' ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        )
    ).fetchone()
