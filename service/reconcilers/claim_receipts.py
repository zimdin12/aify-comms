"""Give a message back when the run that claimed it died without ever being read.

THE LOSS, reported by an external review 2026-08-18 (H1) and ruled by comms-senior-dev the same day.
`dispatch_claim.py` writes a read receipt for every source message of a run AT CLAIM TIME — before
any turn starts — and nothing ever removed it. Unread is computed as the ABSENCE of a receipt
(`routers/agents/listen.py` LEFT JOINs and keeps rows `WHERE r.message_id IS NULL`), so a run that
was claimed and then FAILED without the target ever starting left its source message suppressed for
that agent PERMANENTLY. Not marked read — invisible.

That is the best available explanation for the field reports of agents that accept sends and never
process them, because it is the only one that explains PERMANENCE: the turn-start-fail bug alone
would strand a run, but the message would still be readable.

WHY THIS IS A SWEEP AND NOT A LINE IN EACH FAILURE PATH. Fourteen different writers can move a run
to `failed`/`cancelled`, and this repo has already paid for the event-based version of this mistake:
a spawn sat `running` for 97 minutes because `report_terminal_dead` was one of ~26 terminal writers
and simply was not called (`590e995`). Cleanup that must hold for EVERY path keys on the STATE. A
fifteenth failure path added next month is covered by this without anyone remembering it exists.

WHAT MAKES IT SAFE TO DELETE — the timestamp, which is exact rather than approximate. The claim
inserts the receipt with `read_at = claimed_at`, and it uses `INSERT OR IGNORE`, so a receipt the
agent had ALREADY earned by genuinely reading the message is left untouched with its own `read_at`.
Matching on `read_at = claimed_at` therefore deletes precisely the rows the claim created and cannot
touch an earned one. It is also what makes this idempotent: after the first pass nothing matches, so
a message cannot be resurrected over and over into an agent's inbox.

SCOPE, stated honestly rather than generously:
  * TERMINAL runs only (`failed`/`cancelled`) that never started. A REQUEUED run also loses its
    `claimed_at` (`recovery_writes.py` clears it), but a requeued run is still going to be delivered
    — it has a live path — so it is not the loss case and is deliberately left alone.
  * A run that was requeued and only LATER failed has no `claimed_at` left to match, so its receipt
    is not recoverable this way. That residue is named here rather than papered over.
  * Historical rows ARE repaired, because the sweep does not filter on age — but only where the run
    still exists in `dispatch_runs` (pruning removes old ones) and its `claimed_at` still matches.
    This does NOT recover every message stranded before today, and must not be described as if it
    does.
"""

from __future__ import annotations

import logging

from service.api_core.claim_gating import _dispatch_source_message_ids
from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES

logger = logging.getLogger(__name__)

# Terminal without ever having started: the run is over and the target never saw the content.
#
# DERIVED from the canonical set, not spelled out again. `test_status_set_literal_twins_are_frozen`
# exists because twenty-two queries hardcode status sets that their canonical constant no longer
# agrees with -- the `lost` incident, where a status missing from a hand-written list read as "live"
# forever. Writing ("failed", "cancelled") here would have been a twenty-third twin, and the gate
# caught it. Subtracting instead means a new terminal status is covered by this sweep automatically,
# which is the behaviour we would want: any way a run can END without the target starting is a way a
# message can be stranded.
#
# `completed` is the one terminal status that must NOT release: it means the work was done, so the
# message was genuinely consumed.
_UNSTARTED_TERMINAL_STATUSES = tuple(sorted(_DISPATCH_TERMINAL_STATUSES - {"completed"}))


async def _release_receipts_from_unstarted_runs(db, *, limit: int = 200) -> int:
    """Delete claim-written receipts for runs that ended without the target starting a turn.

    Returns the number of receipts released, so the sweep line reports work done rather than work
    considered — the same distinction `_mark_dispatch_source_messages_read` makes on the way in.
    """
    placeholders = ",".join("?" for _ in _UNSTARTED_TERMINAL_STATUSES)
    cursor = await db.execute(
        f"""
        SELECT id, status, target_agent, message_id, body, claimed_at
        FROM dispatch_runs
        WHERE status IN ({placeholders})
          AND claimed_at IS NOT NULL AND claimed_at != ''
          AND (started_at IS NULL OR started_at = '')
        ORDER BY finished_at DESC, rowid DESC
        LIMIT ?
        """,
        (*_UNSTARTED_TERMINAL_STATUSES, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    released = 0
    for row in rows:
        target = str(row["target_agent"] or "").strip()
        claimed_at = str(row["claimed_at"] or "").strip()
        if not target or not claimed_at:
            continue
        message_ids = _dispatch_source_message_ids(row)
        if not message_ids:
            continue
        ids_placeholders = ",".join("?" for _ in message_ids)
        result = await db.execute(
            f"""
            DELETE FROM read_receipts
            WHERE agent_id = ?
              AND read_at = ?
              AND message_id IN ({ids_placeholders})
            """,
            (target, claimed_at, *message_ids),
        )
        count = int(getattr(result, "rowcount", 0) or 0)
        if count > 0:
            released += count
            logger.info(
                "Released %d read receipt(s) for %s: run %s was claimed at %s and ended %s without "
                "starting a turn",
                count, target, row["id"], claimed_at, str(row["status"] or ""),
            )
    return released
