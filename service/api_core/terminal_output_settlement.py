"""What an output append has to settle before it can just append output.

Extracted from `append_terminal_output` in `service/routers/terminals.py` in v0.5.4;
`test_append_terminal_output_split_is_inert.py` inlines both helpers back and AST-compares against the
pre-split fixture. Bodies are at their original 8-space column so the literals inside are preserved
byte-for-byte.

The route is the hottest write path in the service — a live PTY posts every 1-4 seconds — and these
two blocks are the parts of it that are NOT appending: deciding whether a different bridge may take
the terminal over, and tearing down what a terminal-ending status invalidates.
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from service.api_core.events import _append_terminal_event
from service.api_core.terminal_controls_io import _clear_console_terminal_binding
from service.clock import now as _now
from service.reconcilers.terminal_runs import _close_active_terminal_runs_for_terminal
from service.terminal_diagnostics import terminal_end_summary


async def _settle_bridge_takeover_for_output(db, terminal, terminal_id: str, new_bridge_id: str,
                                             existing_bridge_id: str, is_virtual_rpc: bool) -> None:
        """A DIFFERENT bridge is posting output for this terminal. Decide whether that is allowed.

        For a virtual RPC terminal it IS: ownership transfers and a stopped row is revived, because the
        bridge-supersession cleanup can race an in-flight dispatch on the new bridge and leave a
        terminal marked stopped that something is actively using. The transfer is audited so the
        takeover is visible in the event log rather than only in the row.

        For anything else it is a 409. A real PTY has one owner, and silently accepting output from a
        second bridge would interleave two processes into one screen.
        """
        if new_bridge_id and existing_bridge_id and new_bridge_id != existing_bridge_id:
            if is_virtual_rpc:
                # Transfer ownership of the synth terminal to the new bridge.
                # Audit so operators see the takeover in the event log.
                #
                # Revive if previously stopped — the bridge-supersession
                # cleanup (`_stop_virtual_terminals_for_superseded_bridges`)
                # can race against an in-flight dispatch on the new bridge:
                # supersession stops the row, then the new bridge's
                # /output POST arrives. Operator-reported 2026-05-22:
                # codex synth terminal showed "started then stopped" yet
                # the agent still replied — frames were accumulating
                # in terminal_events while the row was stale-stopped,
                # leaving the dashboard rendering "terminal is not
                # running" despite a healthy stream of frames. The
                # arriving POST is hard proof the new bridge is
                # actively writing, so undo the stale stop.
                current_status = str(terminal["status"] or "").strip().lower()
                if current_status == "stopped":
                    await db.execute(
                        """
                        UPDATE terminal_sessions
                        SET bridge_id = ?, status = 'running', stopped_at = NULL, error = ''
                        WHERE id = ?
                        """,
                        (new_bridge_id, terminal_id),
                    )
                else:
                    await db.execute(
                        "UPDATE terminal_sessions SET bridge_id = ? WHERE id = ?",
                        (new_bridge_id, terminal_id),
                    )
                await _append_terminal_event(
                    db,
                    terminal_id,
                    "virtual_rpc_bridge_takeover",
                    json.dumps({
                        "from": existing_bridge_id,
                        "to": new_bridge_id,
                        "revived": current_status == "stopped",
                    }),
                )
                # Commit immediately — the endpoint's only other commit
                # is inside the _TERMINAL_END_STATUSES branch, which
                # doesn't fire for normal "running" output POSTs. Without
                # this, the bridge_id transfer + revive would silently
                # be lost on the next connection (failing the takeover
                # contract for any subsequent reader).
                await db.commit()
            else:
                raise HTTPException(409, "Terminal is owned by a different bridge")


async def _close_out_terminal_on_end_status(db, terminal, terminal_id: str, status: str,
                                            _TERMINAL_END_STATUSES) -> None:
        """A terminal-ending status arrived with the output. Close what it invalidates.

        The set is PASSED IN rather than imported, so the caller keeps ownership of which statuses end
        a terminal. It is read in several places in the router, and a second copy here is the
        forked-constant class this series exists to remove — the kind that fails quietly, because the
        copies agree until someone adds a status to one and then a terminal ends without its runs
        being closed.

        THE PARAMETER IS SCREAMING-CASE ON PURPOSE, which looks wrong and is required. The
        extract-method gate splices a helper's body back over its call WITHOUT substituting arguments,
        so it cannot see a value swap; it therefore refuses any call whose argument name differs from
        the parameter it fills. Naming the parameter after the constant is what keeps the round trip
        able to prove anything about this block.
        """
        if status in _TERMINAL_END_STATUSES:
            now = _now()
            # HOW IT ENDED, read back rather than assumed. `_record_terminal_exit` wrote and committed
            # the exit code and signal on this same connection a few lines earlier in the request, so
            # this SELECT sees them; the `terminal` row in hand was read BEFORE that write and does
            # not carry them.
            #
            # ONE EXTRA QUERY, ON THE ENDING PATH ONLY. This branch runs when a terminal-ending status
            # arrives -- once per terminal, not per output chunk -- so it does not touch the hot
            # ingest path this module's high-frequency half lives on.
            #
            # Read here instead of threaded down from the caller because the caller's values are
            # expressions (`req.exitCode`), and the extract-method gate that proves this helper still
            # inlines back into `append_terminal_output` refuses a call whose argument name differs
            # from the parameter it fills. Reading the row keeps the signature, and with it the proof.
            exit_row = await (await db.execute(
                "SELECT exit_code, exit_signal FROM terminal_sessions WHERE id = ?", (terminal_id,),
            )).fetchone()
            summary = terminal_end_summary(
                status,
                exit_row["exit_code"] if exit_row is not None else None,
                str((exit_row["exit_signal"] if exit_row is not None else "") or ""),
            )
            await _close_active_terminal_runs_for_terminal(db, terminal, status, now=now, reason=summary)
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?,
                    updated_at = ?,
                    stopped_at = COALESCE(stopped_at, ?)
                WHERE id = ?
                """,
                (status, now, now, terminal_id),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = 'managed',
                    last_seen = ?
                WHERE id = ?
                """,
                (status, now, terminal["session_id"]),
            )
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
            await db.commit()
