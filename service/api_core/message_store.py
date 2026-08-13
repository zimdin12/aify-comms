"""Bulk operations over the `messages` table: delete a set of ids, count unread per agent.

v0.5.4 layer 0. Two helpers that touch the messages table directly and were reached from
`dispatch_messages/shared.py` through borrow shims. They are together because they are the message
STORE — neither composes text, decides delivery, or knows anything about dispatch runs; that separation
is why `api_core/dispatch_text.py` and `api_core/dispatch_run_state.py` exist beside this rather than
absorbing it.

`_delete_messages_by_ids` CHUNKS its deletes. That is not decoration: SQLite has a variable limit per
statement, and an unsent conversation can exceed it, so a single `IN (...)` over an unbounded id list
fails on exactly the large inputs that matter.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback — the caller owns the
transaction. A LEAF: imports one api_core sibling and nothing else.
"""

from __future__ import annotations

from service.api_core.serialization import _dedupe_preserve


async def _delete_messages_by_ids(db, message_ids: list[str], *, chunk_size: int = 250) -> int:
    pending = _dedupe_preserve([str(message_id or "").strip() for message_id in message_ids if str(message_id or "").strip()])
    if not pending:
        return 0

    deleted = 0
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        await db.execute(f"UPDATE messages SET in_reply_to = NULL WHERE in_reply_to IN ({placeholders})", chunk)
        await db.execute(f"UPDATE dispatch_runs SET message_id = NULL WHERE message_id IN ({placeholders})", chunk)
        await db.execute(f"UPDATE dispatch_runs SET in_reply_to = NULL WHERE in_reply_to IN ({placeholders})", chunk)
        # Also clear the reply LINK (bughunt 2026-07-03): if a deleted/unsent message was
        # a run's recorded reply, leaving result_message_id pointing at the now-gone row
        # kept the contract 'answered' with no reply behind it — it never re-opened.
        await db.execute(f"UPDATE dispatch_runs SET result_message_id = NULL WHERE result_message_id IN ({placeholders})", chunk)
        await db.execute(f"UPDATE dispatch_controls SET source_message_id = '' WHERE source_message_id IN ({placeholders})", chunk)
        await db.execute(f"DELETE FROM read_receipts WHERE message_id IN ({placeholders})", chunk)
        cursor = await db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", chunk)
        deleted += cursor.rowcount or 0
    return deleted


async def _get_unread_count_map(db, agent_ids: list[str]) -> dict[str, int]:
    if not agent_ids:
        return {}
    placeholders = ",".join("?" for _ in agent_ids)
    cursor = await db.execute(
        f"""
        SELECT m.to_agent AS agent_id, COUNT(*) AS unread_count
        FROM messages m
        LEFT JOIN read_receipts rr ON m.id = rr.message_id AND rr.agent_id = m.to_agent
        WHERE m.to_agent IN ({placeholders}) AND rr.message_id IS NULL
        GROUP BY m.to_agent
        """,
        tuple(agent_ids),
    )
    rows = await cursor.fetchall()
    return {row["agent_id"]: int(row["unread_count"] or 0) for row in rows}
