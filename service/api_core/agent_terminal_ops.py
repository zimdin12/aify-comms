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

import asyncio


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

#: How long a REMOVE waits for the host to take the stop it just wrote. See `_await_stop_claims`.
#:
#: A live claimer holds a long-poll open, so it takes the control in MILLISECONDS -- this budget is
#: for the gap, not the normal case. Two seconds is long enough that a busy host still wins and short
#: enough that an operator clicking Remove does not think the button is broken.
STOP_CLAIM_WAIT_SECONDS = 2.0

#: How often to look. Fifty reads over the budget, each a single indexed COUNT.
_STOP_CLAIM_POLL_SECONDS = 0.04


async def _pending_stop_controls(db, agent_id: str) -> int:
    """Stop controls for this agent's terminals that no host has taken yet."""
    cursor = await db.execute(
        """
        SELECT COUNT(*) FROM terminal_controls c
        JOIN terminal_sessions t ON t.id = c.terminal_id
        WHERE t.agent_id = ? AND c.action = 'stop' AND c.status = 'pending'
        """,
        (agent_id,),
    )
    row = await cursor.fetchone()
    return int(row[0] or 0) if row else 0


async def _await_stop_claims(
    db, agent_id: str, *,
    budget_seconds: float = STOP_CLAIM_WAIT_SECONDS,
    poll_seconds: float = _STOP_CLAIM_POLL_SECONDS,
    sleep=asyncio.sleep,
    monotonic=None,
) -> bool:
    """Wait, briefly, for the host to take the stop before the agent row is tombstoned.

    THE RACE THIS CLOSES, and it lost three times on the operator's host on 2026-09-07. `REMOVE` is
    STOP-then-tombstone: it writes a stop control, commits, then deletes the agent. But
    `terminal_controls` has `ON DELETE CASCADE` from `terminal_sessions`, which cascades from
    `agents` -- so THE DELETE WIPES THE CONTROL THE SAME REQUEST JUST WROTE. `unregister_agent`'s own
    comment admitted the design depended on the control being "claimed before the tombstone delete",
    and the two commits are milliseconds apart.

    When it loses, nothing tells the host anything. aify-env kept streaming into 404s for ten
    minutes, correctly refusing to kill workers that were still producing, until its own silence
    guard stopped them. Three managed workers, and the only reason it was not worse is that the host
    guards itself.

    A BOUND, NOT A GUARANTEE, and the difference is deliberate. A host that is not listening must not
    be able to block a removal for ever, so the deadline expires and the delete proceeds exactly as
    it does today -- never worse than the behaviour this replaces. What it buys is the ordinary case:
    a live claimer holds a long-poll open and takes the control in milliseconds.

    READ-ONLY WHILE WAITING. The caller commits before calling this, so nothing here holds a write
    transaction open against the single writer.

    @returns whether every stop was claimed before the deadline
    """
    if budget_seconds <= 0:
        return await _pending_stop_controls(db, agent_id) == 0
    clock = monotonic or asyncio.get_event_loop().time
    deadline = clock() + budget_seconds
    while True:
        if await _pending_stop_controls(db, agent_id) == 0:
            return True
        if clock() >= deadline:
            return False
        await sleep(poll_seconds)
