"""Which unread messages a live channel session is about to receive anyway: the queued-channel projection.

The notify hook (mcp/stdio/notify-check.js) prints unread bodies into a Claude session after a tool call. In a
session whose channel producer is live, a message waiting on a QUEUED channel run reaches the model a second time
when that run is claimed, so the hook asks for this projection and leaves those bodies out (design 2, narrowed).

This is NOT a delivery confirmation. A queued run says the channel is the path, never that it will be taken: a
channel may never claim, and a claim marks the source read before its emission can fail. Both gaps stay open
(KNOWN_ISSUES.md) and are why the hook still prints a metadata cue for what it left out.

CONSERVATIVE BY CONSTRUCTION. A message is queued-for-channel only when its newest associated run for this
recipient is unambiguous and is a channel or resident run in `queued`. No run, a newest run in any other state or mode, or
several runs tied at the newest `requested_at` that are not all queued channel runs, keep today's body.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Both modes carry about half of Claude's runs each (live, 2026-10-28).
from service.api_core.claim_gating import CHANNEL_CLAIMED_MODES


def is_queued_for_channel(runs: Iterable[Mapping]) -> bool:
    """Is the newest of these runs, unambiguously, a queued channel run? `runs` are one message's runs for one
    recipient, each with `requested_at`, `execution_mode` and `status`."""
    runs = list(runs)
    if not runs:
        return False
    newest = max(str(run["requested_at"] or "") for run in runs)
    tied = [run for run in runs if str(run["requested_at"] or "") == newest]
    return all(
        str(run["execution_mode"] or "").strip().lower() in CHANNEL_CLAIMED_MODES
        and str(run["status"] or "").strip().lower() == "queued"
        for run in tied
    )


async def queued_for_channel_ids(db, agent_id: str, message_ids: list[str]) -> set[str]:
    """The ids among `message_ids` whose newest run for `agent_id` is a queued channel run. The association is
    the one reply linking uses: the run's own message, or a message batched into it (dispatch_run_items)."""
    if not message_ids:
        return set()
    placeholders = ",".join("?" for _ in message_ids)
    cursor = await db.execute(
        f"""
        SELECT r.message_id AS message_id, r.requested_at, r.execution_mode, r.status
        FROM dispatch_runs r
        WHERE r.target_agent = ? AND r.message_id IN ({placeholders})
        UNION ALL
        SELECT i.message_id AS message_id, r.requested_at, r.execution_mode, r.status
        FROM dispatch_run_items i
        JOIN dispatch_runs r ON r.id = i.run_id
        WHERE r.target_agent = ? AND i.message_id IN ({placeholders})
        """,
        [agent_id, *message_ids, agent_id, *message_ids],
    )
    # A run reached through both associations appears twice, identically, which changes no answer.
    by_message: dict[str, list] = {}
    for row in await cursor.fetchall():
        by_message.setdefault(str(row["message_id"]), []).append(row)
    return {message_id for message_id, runs in by_message.items() if is_queued_for_channel(runs)}
