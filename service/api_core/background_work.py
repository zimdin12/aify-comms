"""A resident claude's background work: shells or agents still running while its prompt is idle.

THE OPERATOR'S ASK, 2026-10-01: "i do not see cyan marker on you currently (but you have shell
running)". The cyan `shell` status existed only for MANAGED workers, read off the screen aify-env
observes; a resident has no screen anyone reads, so it showed `online` with work still running.

THE BRIDGE REPORTS IT, THE SERVICE JUDGES IT. The resident's bridge follows the session transcript,
where Claude Code records every background start (`toolUseResult.backgroundTaskId`, or an async agent
launch) and every finish (a task notification), and posts the count here. A count above zero stamps a
lease the bridge refreshes while the work runs; zero clears it. A bridge that dies stops refreshing,
and the lease expires on its own, so `shell` cannot outlive the evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from service.api_core.serialization import _iso_add_seconds
from service.clock import iso_to_epoch

#: How long one report keeps the work fresh. The bridge refreshes every 5 s while work runs, so a
#: lease outlives three missed refreshes, and `shell` ends within this after the bridge goes quiet.
BACKGROUND_WORK_LEASE_SECONDS = 20


def background_work_lease(background_at: str, now_epoch: float) -> tuple[bool, str]:
    """PURE. Whether a stamped lease is fresh at `now_epoch`, and when it stops being fresh.

    Returns `(fresh, expires_at)`; `expires_at` is "" when the lease is not fresh, so a cache that
    holds a `shell` verdict knows when to recompute it.
    """
    stamped = str(background_at or "").strip()
    seen = iso_to_epoch(stamped)
    if not seen or now_epoch - seen > BACKGROUND_WORK_LEASE_SECONDS:
        return False, ""
    return True, _iso_add_seconds(stamped, BACKGROUND_WORK_LEASE_SECONDS + 1)


async def record_background_work(db, agent_id: str, count: int, now: str) -> None:
    """Stamp the lease while `count` is above zero, clear it at zero. `working_at` is left as it is."""
    stamp = now if count > 0 else ""
    await db.execute(
        "INSERT INTO agent_console_signal (agent_id, working_at, background_at) VALUES (?, '', ?) "
        "ON CONFLICT(agent_id) DO UPDATE SET background_at = excluded.background_at",
        (agent_id, stamp),
    )


def background_work_from_row(row) -> tuple[bool, str]:
    """The lease in an `agent_console_signal` row the caller already read, now. Both status-input
    producers come through here, so they cannot disagree about it; a row without the column, or no
    row, is no evidence of running work."""
    if not row or "background_at" not in row.keys():
        return False, ""
    return background_work_lease(str(row["background_at"] or ""), datetime.now(timezone.utc).timestamp())


async def background_work_for(db, agent_id: str) -> tuple[bool, str]:
    """The agent's lease, read on its own: for the authoritative producer, which reads nothing else
    from the console signal. A read failure is not fresh."""
    try:
        row = await (await db.execute(
            "SELECT background_at FROM agent_console_signal WHERE agent_id = ?", (agent_id,),
        )).fetchone()
    except Exception:
        return False, ""
    return background_work_from_row(row)
