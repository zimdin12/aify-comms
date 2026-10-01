"""Finish every removal a host has reported `done` whose consequence here is still owed (P0 C4).

THE HOST DOES NOT COME BACK FOR IT. The result route commits the host's receipt and then runs the
removal, and a failure between the two left the request `done` with its consequence `pending`. The host
claims only pending and claimed requests, so it never reported that one again: the agent stayed, with
no tombstone, while the host's next snapshot withdrew its definition (review of P4, N2). Completion
that waits for an event nobody sends is no completion, so this sweep finishes them from the stored
state alone, whichever report or crash left them owed.

FAIR ACROSS THE POPULATION. A pass is bounded, and a removal that fails is stamped and read after every
owed removal that has not failed, oldest failure first. Ordered by age alone, fifty that kept failing
were the fifty read every pass, and a newer owed removal behind them was never tried (review of P4, N4).

`finish_removal` carries every fence: it reads `settled_refusal` and the custody fence inside the
transaction that decides, so a sweep racing a report runs the removal once, and a removal whose
definition has since moved to another store or lifetime removes nothing and says so.
"""

from __future__ import annotations

import logging

from service.api_core.definition_requests import finish_removal, request_by_id
from service.clock import now as _now

log = logging.getLogger(__name__)


async def finish_owed_removals(db, limit: int = 50) -> int:
    """Run the consequence of each owed `done` removal. Returns how many were attempted. Commits."""
    rows = await (await db.execute(
        "SELECT id FROM definition_requests WHERE status = 'done' AND consequence = 'pending' "
        "ORDER BY consequence_failed_at, finished_at, id LIMIT ?", (limit,))).fetchall()
    attempted = 0
    for row in rows:
        request = await request_by_id(db, row["id"])
        if request is None or request["consequence"] != "pending":
            continue
        attempted += 1
        try:
            await finish_removal(db, request)
        except Exception:  # one owed removal that fails must not keep the others owed
            await db.rollback()
            log.exception("owed removal %s not finished; a later sweep tries it again", row["id"])
            await db.execute("UPDATE definition_requests SET consequence_failed_at = ? WHERE id = ?", (_now(), row["id"]))
            await db.commit()
    return attempted
