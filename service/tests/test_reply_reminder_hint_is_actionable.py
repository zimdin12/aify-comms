"""A reply reminder must emit a VALID comms_send call and must not claim mechanisms that don't exist.

History, because both directions have now been wrong:

1. The original snippet was `body="<answer, blocker, or result>"` — a conventional placeholder.
2. I "fixed" the placeholder by moving the body out of the call and describing it in prose. That
   produced an INVALID call: `body` is a required zod field (mcp/stdio/server.js:4472
   `body: z.string()`, not `.optional()`), so an agent running the snippet verbatim gets a
   validation error. Strictly worse than a placeholder.
3. The same change asserted "the subject matches it to the run". Also false:
   `_link_reply_message_to_dispatch_run` matches on `WHERE target_agent = ? AND message_id = ?`
   keyed on the reply's inReplyTo, and never reads the subject.

These tests pin the call VALID and pin the false claim out.
"""
import re
import unittest

from service.routers import api_v2


class _Row(dict):
    def keys(self):  # pragma: no cover - trivial
        return super().keys()


def _row(**over):
    base = {
        "id": "run_test_1",
        "message_id": "",
        "target_agent": "mc-senior-dev",
        "from_agent": "dashboard",
        "subject": "Restart mc-senior-dev",
    }
    base.update(over)
    return _Row(base)


REQUIRED_ARGS = ("from=", "to=", "type=", "subject=", "body=")


class ReplyReminderHintIsActionableTests(unittest.TestCase):
    def _text(self, row, full=False):
        return api_v2._contract_reminder_body(row, full=full)

    def test_snippet_is_a_valid_comms_send_call(self):
        """THE REGRESSION GUARD. `body` is required — omitting it makes the snippet unrunnable."""
        for full in (False, True):
            for label, row in (("no-message", _row()), ("threaded", _row(message_id="msg_1"))):
                with self.subTest(full=full, row=label):
                    text = self._text(row, full=full)
                    call = re.search(r"comms_send\([^)]*\)", text)
                    self.assertIsNotNone(call, "a comms_send snippet must be present")
                    for arg in REQUIRED_ARGS:
                        self.assertIn(
                            arg, call.group(0),
                            f"{arg} must be INSIDE the comms_send call — body is a required field, "
                            "so a snippet without it cannot be run",
                        )

    def test_no_subject_based_matching_claim(self):
        """The matcher keys on message_id via inReplyTo; it never reads the subject. Any text
        promising otherwise misleads the agent into thinking its reply will link."""
        for full in (False, True):
            for row in (_row(), _row(message_id="msg_1")):
                text = self._text(row, full=full).lower()
                self.assertNotIn("subject matches", text)
                self.assertNotIn("matched to the run", text)
                self.assertNotIn("match it to the run", text)

    def test_threaded_run_carries_in_reply_to(self):
        text = self._text(_row(message_id="msg_42", from_agent="mc-manager"))
        self.assertIn('inReplyTo="msg_42"', text)
        self.assertIn('subject="Re: Restart mc-senior-dev"', text)

    def test_operator_initiated_run_is_labelled_honestly(self):
        """No message id means the reply genuinely cannot be threaded. Name the run and say so,
        rather than implying an anchor exists."""
        text = self._text(_row())
        # The run id comes from the LIGHT prefix ("Reply owed to <run id>") — the suffix must not
        # repeat it, so assert presence once rather than adding a second copy.
        self.assertEqual(text.count("run_test_1"), 1)
        self.assertIn("no source message", text.lower())
        self.assertNotIn('inReplyTo=', text, "there is no message id to thread to")

    def test_no_unaddressable_sender_placeholder(self):
        """`original-sender` was not a real agent id — a reply addressed to it can never land."""
        text = self._text(_row(from_agent=""))
        self.assertNotIn("original-sender", text)
        self.assertIn('to="dashboard"', text)

    def test_light_reminder_stays_one_line(self):
        """The LIGHT format exists to avoid context burn (2026-07-02 operator decision)."""
        text = self._text(_row(), full=False)
        self.assertNotIn("\n", text)
        self.assertEqual(text.count("run_test_1"), 1, "the run id must not be repeated")


if __name__ == "__main__":
    unittest.main()
