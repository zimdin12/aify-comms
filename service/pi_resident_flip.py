"""The pi delivery flip: drain a resident pi agent's queue, then flip it to the native path.

v0.5.4. Moved out of the control plane, and to SERVICE level rather than to `api_core/` for one specific
reason: it OPENS ITS OWN CONNECTION and COMMITS. The api_core leaf rule is that a leaf takes `db` and owns
no transaction, so a transaction owner does not belong there however well its subject fits. Its siblings
here are `service/terminal_write_queue.py` and `service/dispatch_claim.py`, which are at this level for the
same reason.

That is a placement rule doing real work rather than bookkeeping: dropping this into api_core would have
put a `get_db()` + `commit()` inside the layer whose whole guarantee is that the CALLER owns the
transaction, and nothing in the suite would have objected.

A LEAF in the import sense: `service/db.py`, `service/clock.py` and two api_core siblings. It does not
import a router and does not import the control plane, which is now a caller.
"""

from __future__ import annotations

import asyncio
import json

from service.api_core.capabilities import _default_capabilities_for
from service.api_core.serialization import _json_loads_or
from service.clock import now as _now
from service.db import get_db


async def _drain_and_flip_pi_resident_agents() -> int:
    """Pi delivery flip (Plan 2, 2026-05-25).

    Every ~5s the periodic loop calls this helper. For each pi agent
    marked with runtime_state.pi_resident_pending_flip == True it checks
    that no active or queued dispatch run is currently targeting the
    agent. When clear, the agent migrates from sessionMode=resident to
    sessionMode=managed: session_handle is preserved, capabilities are
    recomputed via _default_capabilities_for (PiAdapter no longer
    supports_resident), the pending-flip flag is cleared, and a
    flipped_at timestamp is recorded.
    """
    db = await get_db()
    try:
        now_iso = _now()
        cursor = await db.execute(
            """
            SELECT id, session_handle, runtime_state, runtime_config
            FROM agents
            WHERE runtime = 'pi'
              AND session_mode = 'resident'
            """
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0
        still_waiting = 0

        for row in rows:
            runtime_state = _json_loads_or(row["runtime_state"], {})
            # Plan 2 backfill: any pi-resident agent is flip-eligible by
            # definition (PiAdapter no longer supports_resident). The
            # pi_resident_pending_flip marker stays useful as a
            # "newly-detected" signal but is not the only gate — agents
            # registered before the Task 16 marker rolled out would
            # otherwise never flip without manual re-registration.

            # Block the flip while any open run is targeting the agent.
            run_cursor = await db.execute(
                """
                SELECT COUNT(*) AS cnt FROM dispatch_runs
                WHERE target_agent = ?
                  AND status IN ('queued', 'claimed', 'running')
                """,
                (row["id"],),
            )
            run_row = await run_cursor.fetchone()
            if run_row and int(run_row["cnt"] or 0) > 0:
                still_waiting += 1
                continue  # wait until next tick

            runtime_state["pi_resident_pending_flip"] = False
            runtime_state["flipped_at"] = now_iso

            runtime_config = _json_loads_or(row["runtime_config"], {})
            new_caps = _default_capabilities_for(
                "pi",
                "managed",
                str(row["session_handle"] or ""),
                runtime_config,
            )

            await db.execute(
                """
                UPDATE agents
                SET session_mode = 'managed',
                    runtime_state = ?,
                    capabilities = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(runtime_state),
                    json.dumps(new_caps),
                    now_iso,
                    row["id"],
                ),
            )
        await db.commit()
        return still_waiting
    finally:
        await db.close()

# The loop that drives the drain above. It lived in the control plane only because the lifespan
# wiring in service/main.py imported it from there; main.py now reaches the owner directly.
#: While a pi agent is waiting to flip, it is looked at this often -- the latency this loop always had.
FLIP_CHECK_WHILE_WAITING_S = 5.0

#: With none waiting, how long before looking again unprompted.
#:
#: MEASURED REASON, 2026-09-16: the idle service spent about 2% of a core, and this loop opened a
#: connection every five seconds on a host with no pi agent at all. A registration that asks for a flip
#: wakes the loop at once (`request_pi_flip_check`), so the common case keeps its five-second latency.
#: The other writers of `session_mode` -- recovery, the mode switch, an import -- are NOT hooked, on
#: purpose: a list of writers is the thing that goes stale. They are caught by this interval instead, so
#: the most a pi agent put into resident mode that way waits is this many seconds.
FLIP_CHECK_WHEN_NONE_S = 30.0

_wake: asyncio.Event | None = None


def request_pi_flip_check() -> None:
    """Look for pi agents to flip now rather than at the next interval. Safe to call from any request.

    Call it AFTER the write that created the candidate has committed: the loop reads through its own
    connection, and a look that runs before the commit finds nothing and backs off.
    """
    if _wake is not None:
        _wake.set()


def next_flip_check_delay(still_waiting: int) -> float:
    """How long to wait before the next look, given how many agents the last one left waiting."""
    return FLIP_CHECK_WHILE_WAITING_S if still_waiting else FLIP_CHECK_WHEN_NONE_S


async def _periodic_pi_resident_flip_loop() -> None:
    """Background loop: drain & flip pi resident agents, often while one waits and rarely otherwise.

    Best-effort: any exception during a tick is swallowed and the next look comes at the short interval.
    Wired into the FastAPI lifespan in service/main.py.
    """
    global _wake
    _wake = asyncio.Event()
    # THE FIRST LOOK IS SOON: a pending flip is stored in the database and survives a restart.
    delay = FLIP_CHECK_WHILE_WAITING_S
    while True:
        try:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            # Cleared BEFORE the look, so a request arriving while it runs wakes the next one.
            _wake.clear()
            delay = next_flip_check_delay(await _drain_and_flip_pi_resident_agents())
        except asyncio.CancelledError:
            raise
        except Exception:
            delay = FLIP_CHECK_WHILE_WAITING_S
