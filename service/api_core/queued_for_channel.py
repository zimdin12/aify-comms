"""Which unread messages a live channel session is about to receive anyway: the queued-channel projection.

The notify hook (mcp/stdio/notify-check.js) prints unread bodies into a Claude session after a tool call. In a
session whose channel producer is live, a message waiting on a QUEUED channel run reaches the model a second time
when that run is claimed, so the hook asks the inbox to leave those bodies out (design 2, narrowed).

This is NOT a delivery confirmation. A queued run says the channel is the path, never that it will be taken: a
channel may never claim, and a claim marks the source read before its emission can fail. Both gaps stay open
(KNOWN_ISSUES.md) and are why the hook still names what was left out.

CONSERVATIVE BY CONSTRUCTION, and ONE RULE IN ONE PLACE: this SQL. A message is queued-for-channel only when, among
its runs for this reader, the newest `requested_at` holds exactly ONE distinct run, and that run is `queued` in a
mode the session's producer claims. No run, a newest run in any other state or mode, or two distinct attempts at
the newest instant keep today's body. A run reached through both associations (its own `message_id` and a
`dispatch_run_items` row) is one attempt: the UNION dedupes it by run id.

It is SQL so the inbox can EXCLUDE these messages in the query that pages it: a filter applied page by page in the
hook could not page honestly past a backlog of them (review of 2df43e00).
"""

from __future__ import annotations

from service.api_core.claim_gating import CHANNEL_CLAIMED_MODES

#: How many of the newest queued attempts the inbox names; the rest are counted.
QUEUED_WINDOW = 20


def queued_for_channel_select(agent_id: str, candidates_sql: str, candidate_params: list) -> tuple[str, list]:
    """A SELECT of (message_id, run_id, requested_at) for each candidate message queued for this reader's channel.
    `candidates_sql` is a subquery or placeholder list naming the message ids to judge."""
    modes = sorted(CHANNEL_CLAIMED_MODES)
    mode_marks = ",".join("?" for _ in modes)
    sql = f"""
        SELECT message_id, MAX(run_id) AS run_id, MAX(requested_at) AS requested_at
        FROM (
            SELECT a.*, MAX(a.requested_at) OVER (PARTITION BY a.message_id) AS newest_at
            FROM (
                SELECT r.message_id AS message_id, r.id AS run_id, r.requested_at, r.execution_mode, r.status
                FROM dispatch_runs r
                WHERE r.target_agent = ? AND r.message_id IN ({candidates_sql})
                UNION
                SELECT i.message_id, r.id, r.requested_at, r.execution_mode, r.status
                FROM dispatch_run_items i JOIN dispatch_runs r ON r.id = i.run_id
                WHERE r.target_agent = ? AND i.message_id IN ({candidates_sql})
            ) a
        )
        WHERE requested_at = newest_at
        GROUP BY message_id
        HAVING COUNT(DISTINCT run_id) = 1
           AND MAX(status) = 'queued'
           AND LOWER(MAX(execution_mode)) IN ({mode_marks})
    """
    return sql, [agent_id, *candidate_params, agent_id, *candidate_params, *modes]


#: This reader's unread message ids, as a subquery taking (agent_id, agent_id).
UNREAD_IDS = ("SELECT u.id FROM messages u LEFT JOIN read_receipts ur ON u.id = ur.message_id AND ur.agent_id = ? "
              "WHERE u.to_agent = ? AND ur.message_id IS NULL")


async def queued_window(db, agent_id: str) -> dict:
    """How many unread messages wait on a queued run for this reader's channel, and the newest `QUEUED_WINDOW` of
    them, each as messageId, sender, and the run that holds it (runId, requestedAt): recovery names messages, the
    hook's watermark orders runs. `total` counts MESSAGES, several of which can share one run."""
    sql, params = queued_for_channel_select(agent_id, UNREAD_IDS, [agent_id, agent_id])
    count = await db.execute(f"SELECT COUNT(*) FROM ({sql})", params)
    total = (await count.fetchone())[0]
    cursor = await db.execute(
        f"SELECT q.message_id, q.run_id, q.requested_at, m.from_agent FROM ({sql}) q JOIN messages m ON m.id = q.message_id "
        "ORDER BY q.requested_at DESC, q.run_id DESC LIMIT ?",
        [*params, QUEUED_WINDOW],
    )
    newest = [
        {"messageId": row["message_id"], "from": row["from_agent"], "requestedAt": row["requested_at"], "runId": row["run_id"]}
        for row in await cursor.fetchall()
    ]
    return {"total": total, "newest": newest}


async def queued_for_channel_ids(db, agent_id: str, message_ids: list[str]) -> set[str]:
    """The ids among `message_ids` queued for this reader's channel."""
    if not message_ids:
        return set()
    sql, params = queued_for_channel_select(agent_id, ",".join("?" for _ in message_ids), list(message_ids))
    cursor = await db.execute(f"SELECT message_id FROM ({sql})", params)
    return {str(row["message_id"]) for row in await cursor.fetchall()}
