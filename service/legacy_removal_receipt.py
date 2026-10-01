"""What a done removal written before `definition_requests.consequence` existed says the service did.

12766276 made that table without the column. Its result route committed the host's `done` first and
then ran the removal, recording only a refusal: it appended `[service: <why>]` to the outcome, again
on every repeated report. So one of its receipts reads, in this order:

- a trailing service note: the service refused. That fact is kept, as the FIRST note says it;
- no note, and the agent's row gone with a tombstone: the service removed it;
- no note, and the agent still there: nothing recorded what the service did, because it did nothing
  yet (the removal failed after the report committed). It is owed: `pending`, and the next report asks
  the removal fence as it would for any owed removal.

A host's own outcome that happened to end in the note's exact form could not be told apart from a
note; 12766276 kept both in one field.
"""
from __future__ import annotations

import re

_TRAILING_NOTES = re.compile(r"\[service: ([^\]]*)\](?:\s*\[service: [^\]]*\])*\s*$")
_OLD_SUFFIX = "; nothing removed"


def legacy_removal_consequence(outcome: str, agent_row_exists: bool, tombstoned: bool) -> str:
    """PURE: the `consequence` a pre-column done removal migrates to."""
    note = _TRAILING_NOTES.search(outcome or "")
    if note:
        why = note.group(1)
        return f"nothing removed: {why[:-len(_OLD_SUFFIX)] if why.endswith(_OLD_SUFFIX) else why}"
    if not agent_row_exists and tombstoned:
        return "removed"
    return "pending"
