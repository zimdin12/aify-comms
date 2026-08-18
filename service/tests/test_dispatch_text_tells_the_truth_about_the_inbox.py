"""The instruction a dispatched agent is given must actually work when followed.

TWO PROBE AGENTS REPORTED THIS UNPROMPTED, 2026-08-18, in the first message each of them sent:
*"comms_inbox(filter=all) is empty"* and *"ready; inbox had 0 message(s)"*. Both had just been told,
in the dispatch text itself, that the full details were in their inbox.

The text was not merely unhelpful — it was FALSE. A dispatched message is marked read at CLAIM time
(the receipt is written before the turn starts), and `comms_inbox` defaults to unread-only. So an
agent that follows "read it in the inbox" with the obvious call gets an empty list and reasonably
concludes the message does not exist. One probe went further and worried its run would not close.

Fetching BY ID works regardless of read state, so naming that call is what makes the instruction
true. This test pins that the text names a call that works, and — more importantly — that it warns
about the one that does not.
"""

from __future__ import annotations

import unittest

from service.api_core.dispatch_text import _render_pending_dispatch_item


def _item(**kwargs) -> str:
    base = dict(
        index=1, from_agent="sender", message_type="request", subject="do the thing",
        body="the full body text", priority="normal", message_id="msg-42",
        in_reply_to="", requested_at="2026-08-18T00:00:00Z",
    )
    base.update(kwargs)
    index = base.pop("index")
    return _render_pending_dispatch_item(index, **base)


class TheDispatchTextTellsTheTruth(unittest.TestCase):
    def test_it_names_a_call_that_actually_returns_the_message(self):
        rendered = _item()
        self.assertIn("messageId=", rendered,
                      "the text does not name the by-id fetch, which is the only call that works")
        self.assertIn("msg-42", rendered, "the id the agent needs is not in the text")

    def test_it_WARNS_that_a_plain_inbox_call_will_not_list_it(self):
        """The half that matters. Without it an agent tries the obvious call, gets nothing, and has
        to work out on its own that "read" does not mean "absent" — which is exactly what both probes
        spent their first reply doing."""
        rendered = _item()
        self.assertRegex(
            rendered, r"already marked read|will NOT list|not list it",
            "the text does not warn that a plain comms_inbox returns nothing for a dispatched message",
        )

    def test_it_does_not_promise_the_inbox_when_there_is_no_message_id(self):
        """A run with no source message has nothing to fetch, so pointing at the inbox would send the
        agent after a message that does not exist at all."""
        rendered = _item(message_id="")
        self.assertNotIn("comms_inbox", rendered,
                         "the text points at the inbox for a run that has no message behind it")

    def test_the_body_preview_still_travels_with_it(self):
        """ANTI-VACUITY: the fix must not have replaced the useful part. An agent that cannot reach
        the inbox at all still needs enough to act on."""
        rendered = _item(body="the full body text")
        self.assertIn("the full body text", rendered)


if __name__ == "__main__":
    unittest.main()
