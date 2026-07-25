"""A reply reminder must hand the agent something it can actually act on.

Live case 2026-07-26, run_1785016147732_61a46d43 ("Restart mc-senior-dev", from=dashboard):
four reminders fired on the configured 10-minute cadence — the loop worked — but each one told
the agent to reply while giving it NO anchor:

    comms_send(from="mc-senior-dev", to="dashboard", type="response", body="<answer, blocker, or result>")

Two defects in one line:
  * `inReplyTo` and `subject` were dropped, because the no-message_id branch omitted them. That
    branch is not an edge case — EVERY dashboard-originated run (Restart/Stop/Start) has no source
    message, so operator-driven contracts always land there. Unanchored, the reply cannot be
    threaded to the run, so the run keeps nagging until it is failed as stranded at 45 min.
  * `body="<answer, blocker, or result>"` was a literal argument inside a snippet the agent is
    meant to run — copyable verbatim as a valid-looking but meaningless answer.
"""
import unittest

from service.routers import api_v2


class _Row(dict):
    """dict that also supports the sqlite3.Row-style access the builder uses."""

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


class ReplyReminderHintIsActionableTests(unittest.TestCase):
    def _hint(self, row, full=False):
        return api_v2._contract_reminder_body(row, full=full)

    def test_placeholder_is_never_a_literal_argument(self):
        """The snippet must be runnable as-is; the body is described in prose, not faked."""
        for full in (False, True):
            for row in (_row(), _row(message_id="msg_1")):
                text = self._hint(row, full=full)
                self.assertNotIn(
                    'body="<', text,
                    "a copyable placeholder argument invites the agent to send it verbatim",
                )
                self.assertIn("body", text.lower(), "the body must still be explained")

    def test_dashboard_originated_run_still_gets_an_anchor(self):
        """THE REGRESSION. No source message must NOT mean no anchor."""
        text = self._hint(_row())
        self.assertIn("run_test_1", text, "the run id must be quoted so the reply can be matched")
        self.assertIn("Restart mc-senior-dev", text, "the subject is the matchable anchor")
        self.assertIn('type="response"', text)
        self.assertIn('from="mc-senior-dev"', text)
        self.assertIn('to="dashboard"', text, "must name the real sender, not a placeholder")

    def test_threaded_run_uses_in_reply_to(self):
        text = self._hint(_row(message_id="msg_42"))
        self.assertIn('inReplyTo="msg_42"', text)
        self.assertIn('subject="Re: Restart mc-senior-dev"', text)

    def test_no_original_sender_placeholder_leaks(self):
        """`original-sender` was a non-addressable literal — sending to it would fail."""
        text = self._hint(_row(from_agent=""))
        self.assertNotIn("original-sender", text)

    def test_both_branches_carry_a_subject(self):
        """Subject is what the unthreaded matcher keys on, so neither branch may omit it."""
        for row in (_row(), _row(message_id="msg_9")):
            self.assertIn("Re: Restart mc-senior-dev", self._hint(row))


if __name__ == "__main__":
    unittest.main()
