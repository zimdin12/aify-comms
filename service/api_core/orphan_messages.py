r"""What makes an unread message an ORPHAN, in one place.

THE DEFECT THIS EXISTS TO FIX, measured on the operator's database 2026-08-29. Two sites asked "is
this message addressed to an agent that is gone?" and both answered it as `a.id IS NULL` -- no row in
`agents`. That is true of every message addressed to `dashboard`, which is not a removed agent at
all: it is the UI's own identity, it has 1,792 unread messages, and it has SENT 3,401.

    unread rows matching the old predicate                     1,891
      addressed to `dashboard`, an active participant          1,792
      addressed to a recipient with an agent_tombstones row        78
      addressed to a recipient deleted before tombstones          21

So `POST /messages/cleanup/orphan-unread`, documented as "Delete unread inbox messages addressed to
removed agents", would have deleted the operator's entire dashboard inbox -- 95% of what it removed
-- and `/stats` reported `orphan_unread_messages: 1891`, a number that invites exactly that click.

A REMOVAL LEAVES A RECORD, and that record is the definition used here. `agent_removal.py` writes an
`agent_tombstones` row when an agent is removed, so "the agent is gone" is a fact the schema states
rather than one inferred from an absence. `dashboard` has no tombstone because it was never removed.

WHAT THIS GIVES UP, said plainly: 21 rows addressed to recipients that vanished before tombstoning
existed (or by a path that did not write one) are no longer cleaned. They are unreachable except
through this endpoint and cost nothing to keep, and "we cannot prove this was a removal" is the
honest answer for them. Deleting on an absence is what produced the 1,792.
"""
from __future__ import annotations

#: The WHERE body shared by the cleanup and the count. Both take `m` as the messages alias and join
#: `read_receipts` themselves, because one needs `SELECT id` for a delete and the other a `COUNT(*)`
#: alongside two other counters in a single pass.
ORPHAN_UNREAD_CONDITIONS = (
    "m.to_agent IS NOT NULL"
    " AND EXISTS (SELECT 1 FROM agent_tombstones t WHERE t.agent_id = m.to_agent)"
)

#: The full predicate, for the caller that has both joins in scope.
#:
#: THREE CONDITIONS, EACH LOAD-BEARING, inherited from the original with the third corrected:
#:   * `m.to_agent IS NOT NULL` -- a CHANNEL BROADCAST row has no recipient (`channel_send.py` writes
#:     one row with no `to_agent` plus a fan-out row per member WITH it). Drop this and every unread
#:     broadcast matches, because the recipient test is trivially true for them.
#:   * the tombstone -- the agent was REMOVED. This was `a.id IS NULL` until 2026-08-29, which is
#:     also true of every identity that was never an agent.
#:   * `r.message_id IS NULL` -- nobody read it. A message already read is history, not an orphan.
ORPHAN_UNREAD_WHERE = ORPHAN_UNREAD_CONDITIONS + " AND r.message_id IS NULL"
