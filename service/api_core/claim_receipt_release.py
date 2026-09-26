"""Give back the read receipts a claim wrote, and only those.

A claim writes a read receipt for each source message of its run with `read_at = claimed_at`
(`_mark_dispatch_source_messages_read`), using INSERT OR IGNORE, so a receipt the agent had already
earned keeps its own `read_at`. Matching `read_at = claimed_at` therefore removes exactly what the claim
wrote and can never remove an earned receipt.

Two kinds of caller: the sweep that releases a run which ended without the target starting a turn
(`reconcilers/claim_receipts.py`), and the two requeue paths, which clear `claimed_at` and so are the
last point at which the claim time is known (v0.7.1 review, W02).
"""

from __future__ import annotations

from service.api_core.claim_gating import _dispatch_source_message_ids


async def release_claim_receipts(db, run_row, *, claimed_at: str = "") -> int:
    """Delete the receipts `run_row`'s claim wrote for its target. Returns how many were deleted.

    `run_row` needs `target_agent`, `message_id` and `body` (the source ids of a merged run are in its
    body); `claimed_at` defaults to the row's own.
    """
    target = str(run_row["target_agent"] or "").strip()
    stamp = str(claimed_at or run_row["claimed_at"] or "").strip()
    message_ids = _dispatch_source_message_ids(run_row)
    if not target or not stamp or not message_ids:
        return 0
    placeholders = ",".join("?" for _ in message_ids)
    result = await db.execute(
        f"DELETE FROM read_receipts WHERE agent_id = ? AND read_at = ? AND message_id IN ({placeholders})",
        (target, stamp, *message_ids),
    )
    return int(getattr(result, "rowcount", 0) or 0)
