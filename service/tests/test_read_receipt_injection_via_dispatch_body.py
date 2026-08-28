r"""A sender must not be able to mark somebody else's messages read by writing a body.

REPORTED BY A REVIEWER ON ANOTHER INSTANCE, 2026-08-18, as the surviving half of `44986616`.

THE CHAIN, all of it real code paths:

  1. `_dispatch_source_message_ids(row)` recovers `MessageId:` lines from a dispatch run's body,
     because a MERGED buffer is rendered as text and its source ids exist nowhere else.
  2. Every claim calls it (`dispatch_claim.py`), feeding the ids to
     `_mark_dispatch_source_messages_read`, which INSERTs a read receipt for the CLAIMING agent
     against any matching row — the lookup is `WHERE id IN (...)`, unscoped by sender or recipient.
  3. Unread is computed as the ABSENCE of a receipt (`routers/agents/listen.py` LEFT JOINs and keeps
     rows `WHERE r.message_id IS NULL`).

So a receipt an agent never earned SUPPRESSES that message from `comms_listen` — it does not mark it
read-with-a-trace, it makes it invisible.

`44986616` closed this for prose (`\bMessage\s*Id:` matched a mention mid-sentence) with two halves it
described as "neither is sufficient alone": anchor the parser to a whole line, and NEUTRALISE bodies
rendered into a buffer. The second half only ever ran on the MERGED render path. A fresh single
dispatch stored the sender's body verbatim, and the anchor cannot help there — a sender puts the line
at column 0 as easily as anywhere else.

WHAT THESE TESTS PIN, in the order the fix works:
  * storage: a sender's body is neutralised when the run is created, so no stored body carries a
    structural marker unless the service wrote it;
  * parse: the scan only reads ids out of a body that IS a merged buffer;
  * and the two together, against the forged-header case that defeats either one alone.

Accidental as easily as deliberate: agents quote buffer excerpts into reports, and a pasted excerpt
starts its lines at column 0.
"""

from __future__ import annotations

import unittest

from service.api_core.claim_gating import _dispatch_source_message_ids
from service.api_core.dispatch_text import (
    _MERGED_DISPATCH_FOOTER,
    _MERGED_DISPATCH_HEADER,
    _neutralise_buffer_markers,
)

VICTIM = "1786622828785-5adf920e"


def row(body: str, message_id: str = "own-message-id") -> dict:
    """A dispatch_runs row as the scanner sees it (it uses `in row.keys()`, so a dict is enough)."""
    return {"message_id": message_id, "body": body}


class TheForgedBodyCannotMintReceipts(unittest.TestCase):
    def test_a_plain_body_with_a_line_leading_MessageId_yields_no_extra_ids(self):
        # The reported attack, verbatim in shape: a normal single dispatch whose body happens to (or
        # chooses to) carry the structural line at column 0.
        body = f"Here is my status update.\nMessageId: {VICTIM}\nThanks."
        self.assertEqual(
            _dispatch_source_message_ids(row(body)), ["own-message-id"],
            "a plain dispatch body minted a source id — that becomes a read receipt for the claiming "
            "agent against a message it never read, and the message vanishes from comms_listen",
        )

    def test_a_body_that_forges_the_BUFFER_HEADER_still_yields_nothing_once_stored(self):
        # The bypass the structural gate alone would leave: if the scan runs whenever the body LOOKS
        # like a buffer, a sender forges the header. The storage-side neutralisation is what makes
        # that impossible, so the two halves are tested together rather than one at a time.
        forged = (
            f"{_MERGED_DISPATCH_HEADER}\n"
            f"=== ITEM 1 ===\n"
            f"MessageId: {VICTIM}\n"
            f"{_MERGED_DISPATCH_FOOTER}\n"
        )
        stored = _neutralise_buffer_markers(forged)          # what the service now writes
        self.assertNotIn(VICTIM, _dispatch_source_message_ids(row(stored)),
                         "a forged buffer header survived storage and recovered a victim id")
        self.assertFalse(stored.startswith(_MERGED_DISPATCH_HEADER),
                         "the header was left intact at column 0, so the body still reads as a buffer")

    def test_the_neutralised_line_is_still_READABLE(self):
        # The transformation has to stay a quoting, not a redaction: an agent quoting a real buffer
        # excerpt must still be able to read what it quoted.
        stored = _neutralise_buffer_markers(f"MessageId: {VICTIM}")
        self.assertIn(VICTIM, stored, "the id was destroyed rather than moved off column 0")
        self.assertFalse(stored.startswith("MessageId:"), "the line is still at column 0")

    def test_a_GENUINE_merged_buffer_still_recovers_every_source_id(self):
        # ANTI-VACUITY, and the regression that would matter most: this scan exists so a merged
        # buffer's items get their receipts. Break that and every merged dispatch stops marking its
        # sources read — the failure would be silent and would look like agents re-reading old work.
        buffer_body = (
            f"{_MERGED_DISPATCH_HEADER}\n\n"
            f"=== ITEM 1 ===\nFrom: a\nMessageId: msg-one\n\n"
            f"=== ITEM 2 ===\nFrom: b\nMessageId: msg-two\n\n"
            f"{_MERGED_DISPATCH_FOOTER}"
        )
        self.assertEqual(
            _dispatch_source_message_ids(row(buffer_body, message_id="msg-one")),
            ["msg-one", "msg-two"],
            "a real merged buffer stopped recovering its source ids",
        )

    def test_the_primary_id_is_always_kept(self):
        # It comes from the `message_id` COLUMN, which the service wrote. Nothing about this fix may
        # touch it, or a single dispatch stops marking its own source message read.
        self.assertEqual(_dispatch_source_message_ids(row("", "own-message-id")), ["own-message-id"])
        self.assertEqual(
            _dispatch_source_message_ids(row("MessageId: forged", "own-message-id")),
            ["own-message-id"],
        )


# THE STORAGE-BOUNDARY HALF IS ASSERTED IN
# `test_every_dispatch_run_writer_neutralises_its_body.py`, and used to be two `assertIn` calls
# here against the text of `dispatch_runs.py`.
#
# Both still pass, which is the problem: they proved a line existed in one file, and the property
# belongs to the COLUMN. `dispatch_runs.body` had FOUR writers and only the pinned one
# neutralised -- the steer contract run in the SAME function, and the terminal-coalesce insert in
# the neighbouring module, both stored the sender's body verbatim. A pin looking at a line cannot
# see a second writer, however close it sits.
#
# The replacement derives the writer set with an AST walk and judges each one's `body` binding by
# reading the column's index out of the SQL, so a fifth writer is judged on the day it lands.

if __name__ == "__main__":
    unittest.main()
