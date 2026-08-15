"""Audit-trail appenders: dispatch events, terminal events, terminal controls.

v0.5.1i, the last high-fanout core family before route domains start moving. These three write the
rows that every post-mortem in this repo is reconstructed from -- when a run stalls or a terminal
dies, `dispatch_events` and `terminal_events` are the evidence -- so they get a home where they can
be read as a unit instead of being three points in a 20,000-line router.

The v0.5 reconcilers were already borrowing all three back through the router; those borrows are
repointed at this module in the same commit, per the reviewer's completion rule.

`_CONTROL_ID_COUNTER` MOVES WITH THEM AND IS THE DELICATE PART. It is an `itertools.count()`, so it
is not a constant at all -- it is mutable process state that mints control ids. A second module-level
assignment would give two importers two independent counters, and the failure would not be an error:
it would be DUPLICATE control ids issued by different call paths, discovered later as controls that
appear to collide or overwrite each other. That is the same silent-forking class as
`_LIVE_STATE_CACHE` and `_SETTINGS_CACHE`, so it is registered in
`service/tests/test_process_global_identity.py` with them.
"""

from __future__ import annotations

import itertools
import json
import time
import uuid

from service.clock import now as _now


_CONTROL_ID_COUNTER = itertools.count()


async def _append_dispatch_event(db, run_id: str, event_type: str, body: str = ""):
    await db.execute(
        "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
        (run_id, event_type, body or "", _now())
    )


_terminal_event_counts: dict[str, int] = {}


_TERMINAL_EVENT_CAP = 500


_TERMINAL_EVENT_PRUNE_EVERY = 200


async def _append_terminal_event(db, terminal_id: str, event_type: str, body: str = ""):
    await db.execute(
        "INSERT INTO terminal_events (terminal_id, event_type, body, created_at) VALUES (?,?,?,?)",
        (terminal_id, event_type, body or "", _now()),
    )
    # terminal_events gets a row per flushed output chunk and is only ever read
    # back LIMIT ~200; without pruning it grows unbounded per terminal for the
    # life of the DB. Amortize the prune (every Nth insert) to keep it bounded
    # without paying a DELETE on every chunk.
    count = _terminal_event_counts.get(terminal_id, 0) + 1
    if count >= _TERMINAL_EVENT_PRUNE_EVERY:
        _terminal_event_counts[terminal_id] = 0
        await db.execute(
            """
            DELETE FROM terminal_events
            WHERE terminal_id = ?
              AND id NOT IN (
                SELECT id FROM terminal_events
                WHERE terminal_id = ?
                ORDER BY id DESC
                LIMIT ?
              )
            """,
            (terminal_id, terminal_id, _TERMINAL_EVENT_CAP),
        )
    else:
        _terminal_event_counts[terminal_id] = count


async def _append_terminal_control(
    db,
    *,
    terminal_id: str,
    environment_id: str,
    bridge_id: str,
    action: str,
    requested_by: str = "dashboard",
    body: str = "",
    cols: int = 0,
    rows: int = 0,
) -> str:
    control_id = f"termctl_{int(time.time() * 1000)}_{next(_CONTROL_ID_COUNTER):06d}_{uuid.uuid4().hex[:8]}"
    if str(action or "").strip().lower() == "resize":
        cursor = await db.execute(
            """
            INSERT INTO terminal_controls (
                id, terminal_id, environment_id, bridge_id, action, body, cols, rows,
                status, requested_by, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(terminal_id) WHERE action = 'resize' AND status = 'pending'
            DO UPDATE SET
                environment_id = excluded.environment_id,
                bridge_id = excluded.bridge_id,
                body = excluded.body,
                cols = excluded.cols,
                rows = excluded.rows,
                requested_by = excluded.requested_by,
                requested_at = excluded.requested_at
            RETURNING id
            """,
            (
                control_id,
                terminal_id,
                environment_id,
                bridge_id,
                "resize",
                body or "",
                int(cols or 0),
                int(rows or 0),
                "pending",
                requested_by or "dashboard",
                _now(),
            ),
        )
        row = await cursor.fetchone()
        return str(row["id"])
    await db.execute(
        """
        INSERT INTO terminal_controls (
            id, terminal_id, environment_id, bridge_id, action, body, cols, rows, status, requested_by, requested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            control_id,
            terminal_id,
            environment_id,
            bridge_id,
            action,
            body or "",
            int(cols or 0),
            int(rows or 0),
            "pending",
            requested_by or "dashboard",
            _now(),
        ),
    )
    return control_id
