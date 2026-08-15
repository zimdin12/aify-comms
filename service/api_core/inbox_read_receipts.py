"""Reading the inbox is not a read. This is everything it writes.

Extracted from `get_inbox` in `service/routers/dispatch_messages/messages.py` in v0.5.4;
`test_get_inbox_split_is_inert.py` inlines it back and AST-compares against the pre-split fixture.
The body is at its original 8-space column so the SQL literals inside are preserved byte-for-byte.

THREE WRITES, ONE GUARD. A non-peek inbox read stamps read receipts, completes dispatch runs that
were stranded by a bridge that died mid-turn, and refreshes the caller's own status. `peek=true`
does none of it, which is the whole reason the parameter exists -- a dashboard poll must be able to
look at an inbox without marking it read or telling the status engine the agent just started working.

THE `status IN ('claimed', 'running')` FILTER IS LOAD-BEARING and is the subtle one. Only runs a
bridge already took are completed here. A QUEUED run must be left alone: it is what the bridge claims
in order to wake the agent as a turn, and completing it from a read would silently delete the wake.
"""
from __future__ import annotations

from service.clock import now as _now


async def _settle_inbox_read(db, messages, agent_id, peek) -> None:
        """Apply what a non-peek inbox read owes: receipts, stranded runs, status.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing. Every argument
        is passed under the caller's own name for the same reason: inline-back does not substitute
        arguments, so it refuses a call whose argument name differs from the parameter it fills.
        """
        if not peek:
            now = _now()
            unread_found = 0
            for msg in messages:
                if not msg["read"]:
                    unread_found += 1
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        (msg["id"], agent_id, now)
                    )
            # Complete stuck dispatch runs linked to messages we just read.
            # Only claimed/running (stuck from dead bridges) — NOT queued.
            # Queued dispatches should be left for the bridge to claim and
            # execute as a turn. Completing them here would prevent the wake.
            if unread_found > 0:
                read_msg_ids = [msg["id"] for msg in messages if not msg["read"]]
                for msg_id in read_msg_ids:
                    await db.execute(
                        """
                        UPDATE dispatch_runs
                        SET status = 'completed', summary = 'Message read via inbox', finished_at = ?
                        WHERE message_id = ? AND target_agent = ? AND status IN ('claimed', 'running')
                        """,
                        (now, msg_id, agent_id),
                    )

            # Smart status: got messages = working, no messages = idle
            new_status = "working" if unread_found > 0 else "idle"
            await db.execute(
                "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE ? END WHERE id = ?",
                (now, new_status, agent_id)
            )
            await db.commit()
