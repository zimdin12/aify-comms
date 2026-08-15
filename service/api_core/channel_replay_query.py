"""Which channel messages a recovering environment still owes a delivery, as one query.

Extracted from `_replay_undelivered_channel_messages_on_env_recovery` in
`service/reconcilers/dispatch_queue.py` in v0.5.4;
`test_replay_undelivered_channel_messages_split_is_inert.py` inlines it back and AST-compares
against the pre-split fixture. The body is at its original 4-space column so the SQL is preserved
byte-for-byte, including the comment that records what it got wrong.

A CANDIDATE IS A MESSAGE THAT WAS MEANT TO BE DELIVERED AND NEVER WAS: it came from a channel, it
has a real recipient, a dispatch was requested for it, no read receipt exists, and no dispatch run
was ever created. All five, or a recovering environment replays something that already landed.

THE TIMESTAMP CONVERSION IS THE DEFECT THIS QUERY IS KNOWN FOR. `messages.timestamp` is epoch
MILLISECONDS, not ISO, and `datetime(1786402075333)` returns NULL -- so the comparison was NULL,
never true, and this reconciler could not match a single row it exists to replay. Measured on the
live database: 0 candidates under the old predicate, 115 under `datetime(m.timestamp / 1000,
'unixepoch')`. The repo calls it the sixth lexical/epoch timestamp bug of its kind, and other code
already did it correctly -- which makes it a copy that drifted rather than a misunderstanding, and
is why the conversion is now tested by EXECUTION rather than by reading it.
"""
from __future__ import annotations


async def _select_undelivered_channel_messages(db, cutoff_param, limit):
    """Return the channel messages still owed a delivery, oldest first.

    `cutoff_param` arrives ready-made so the query needs no clock of its own: a test hands it a
    window rather than arranging for wall time to pass. Every argument is passed under the
    caller's own name, because inline-back does not substitute arguments.
    """
    cursor = await db.execute(
        """
        SELECT m.id, m.from_agent, m.to_agent, m.channel, m.type, m.subject, m.body, m.priority
        FROM messages m
        LEFT JOIN read_receipts rr ON rr.message_id = m.id AND rr.agent_id = m.to_agent
        WHERE m.source = 'channel'
          AND m.to_agent IS NOT NULL AND m.to_agent != '' AND m.to_agent != 'dashboard'
          AND m.dispatch_requested = 1
          -- `messages.timestamp` is epoch MILLISECONDS, not ISO. `datetime(1786402075333)` returns
          -- NULL, so this comparison was NULL — never true — and this reconciler could not match a
          -- single row it exists to replay. Measured on the live DB: 0 candidates under the old
          -- predicate, 115 under this one.
          --
          -- Same class as the `finished_at` guard that excluded its own target rows for two months,
          -- and the sixth lexical/epoch timestamp bug recorded in this repo. Other code already
          -- knew the shape and did it correctly (`datetime(timestamp / 1000, 'unixepoch')`), which
          -- is what makes this a copy that drifted rather than a misunderstanding.
          AND datetime(m.timestamp / 1000, 'unixepoch') >= datetime('now', ?)
          AND rr.message_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM dispatch_runs dr WHERE dr.message_id = m.id)
        ORDER BY m.timestamp ASC
        LIMIT ?
        """,
        (cutoff_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    return rows
