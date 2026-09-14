"""End the terminals a host says it no longer holds a process for.

WHY THIS EXISTS, measured on the operator's host 2026-09-14. After an aify-env restart this service
was never told which terminals had gone: the new daemon starts with an empty handle book and says
nothing about its predecessor's rows, and the old daemon's exit markers were cut off by its own
shutdown. Those rows ended only through the managed ghost reaper -- about 180s of sidecar window and
a 60s sweep -- and a detached hermes gateway's `server.js` child counts as a live wrapper child, which
vetoes that reaper for as long as the gateway runs.

The host is the only tier that knows which processes it runs, so it now says so: every aify-comms
plugin heartbeat carries `metadata.heldTerminals`. On an ACCEPTED beat -- the bridge that owns the
environment row after arbitration; every refusal returns before this is reached -- a confirmed
terminal in that environment which the host does not name is over, and is closed through the same
close-out a terminal-ending output takes.

ABSENT IS NOT EMPTY. An aify-env older than this sends no list, and a beat without one ends nothing.

A LEAF: the router calls it and nothing here reaches back up.
"""

from __future__ import annotations

import json
import time

from service.api_core.events import _append_terminal_event
from service.api_core.terminal_output_settlement import _close_out_terminal_on_end_status
from service.api_core.terminal_status import (
    TERMINAL_LIVE_FILTER_SQL,
    _TERMINAL_ACTIVE_STATUSES,
    _TERMINAL_END_STATUSES,
)
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.clock import iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state

#: How recently a terminal may have been touched and still be spared by an ONLINE beat that does not
#: name it. The race it covers: aify-env builds the list, a start is confirmed (the control PATCH
#: stamps `updated_at`), and the beat built BEFORE that confirmation arrives after it. The host adds a
#: terminal to the list before it reports the start, so the window is one heartbeat's time in flight,
#: which aify-env's client bounds at 10 seconds (its request timeout in `api.mjs`). Twice that, so a slow service is not
#: what ends a worker that just came up.
#:
#: The cost is bounded too: a held terminal's liveness frame refreshes `updated_at` every control
#: pass, a terminal the host does not hold gets none, so it ends on the first beat after this window.
HELD_TERMINALS_GRACE_SECONDS = 20

#: What a terminal the host no longer holds becomes -- the same end status the ghost reaper writes.
ENDED_STATUS = "stopped"

#: Why, on the row and in its event, in words an operator reading a dead console can act on.
ENDED_REASON = "the host no longer holds a process for this terminal (reported by its environment heartbeat)"

#: The statuses a host has CONFIRMED. `starting` is the service's own: the row exists, the host has
#: not reported a process yet, so its absence from the list says nothing.
_CONFIRMED_STATUSES = frozenset(_TERMINAL_ACTIVE_STATUSES - {"starting"})


def terminals_the_host_no_longer_holds(rows, held_terminals, *, now_epoch: float, grace_seconds: float) -> list:
    """The ids among `rows` that the host's list says are over. Pure: no clock, no database.

    Each row needs `id`, `status`, `updated_at` and `command`. A virtual-rpc console is a frame buffer
    with no host process behind it, so no host ever names one and none is selected. An `updated_at`
    that cannot be read is not evidence of age.
    """
    held = {str(terminal_id) for terminal_id in held_terminals}
    selected = []
    for row in rows:
        terminal_id = str(row["id"] or "")
        if not terminal_id or terminal_id in held:
            continue
        if str(row["status"] or "").strip().lower() not in _CONFIRMED_STATUSES:
            continue
        if terminal_id.startswith("vterm_") or str(row["command"] or "") in VIRTUAL_RPC_COMMAND_SET:
            continue
        if grace_seconds:
            touched = iso_to_epoch(row["updated_at"])
            if not touched or now_epoch - touched <= grace_seconds:
                continue
        selected.append(terminal_id)
    return selected


async def end_terminals_the_host_no_longer_holds(db, environment_id: str, held_terminals, *, offline: bool) -> list:
    """Close every terminal in `environment_id` the host's accepted beat does not name.

    CALL ONLY FOR AN ACCEPTED BEAT. `held_terminals` that is not a list ends nothing. An OFFLINE beat
    takes no grace: that host is going and takes its processes with it, so a start it confirmed a
    moment ago is ending anyway.
    """
    if not isinstance(held_terminals, list):
        return []
    rows = await (await db.execute(
        f"""
        SELECT id, session_id, agent_id, status, updated_at, command
        FROM terminal_sessions
        WHERE environment_id = ? AND status IN {TERMINAL_LIVE_FILTER_SQL}
        """,
        (environment_id,),
    )).fetchall()
    ended = terminals_the_host_no_longer_holds(
        rows, held_terminals, now_epoch=time.time(),
        grace_seconds=0 if offline else HELD_TERMINALS_GRACE_SECONDS,
    )
    by_id = {str(row["id"]): row for row in rows}
    for terminal_id in ended:
        terminal = by_id[terminal_id]
        # THE REASON FIRST, so the close-out's own commit carries it.
        await db.execute(
            "UPDATE terminal_sessions SET error = COALESCE(NULLIF(error, ''), ?) WHERE id = ?",
            (ENDED_REASON, terminal_id),
        )
        await _close_out_terminal_on_end_status(db, terminal, terminal_id, ENDED_STATUS, _TERMINAL_END_STATUSES)
        await _append_terminal_event(db, terminal_id, "host_no_longer_holds_terminal", json.dumps({
            "environmentId": environment_id, "previousStatus": str(terminal["status"] or ""),
            "reason": ENDED_REASON,
        }))
        await invalidate_agent_live_state(db, str(terminal["agent_id"] or ""))
    if ended:
        # The close-out commits its own writes; the event rows after it are this function's to commit.
        await db.commit()
    return ended
