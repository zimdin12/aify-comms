"""An `info` reply closes a run only if it CLAIMS completion — not if it merely says the word.

`_message_satisfies_reply_contract` closes a reply contract for an `info` message when the text
signals completion. That test was a bare keyword search over subject+body, and the keyword list is
ordinary English: done, complete, finished, fixed, pushed, committed, shipped, merged, resolved,
verified, ready, answered.

Measured by calling the real function before the guard existed, EIGHT of nine realistic progress
updates closed the contract:

    "Not done yet - still investigating."           -> closed
    "I haven't finished; blocked on the DB lock."   -> closed
    "This is not fixed. Reopening."                 -> closed
    "not ready yet"                                 -> closed
    "No progress; nothing resolved so far."         -> closed
    "Still working on it, will report when done."   -> closed
    "Are you ready for the handoff?"                -> closed
    "Done - pushed to main."                        -> closed  (the only correct one)

Only "Ack, I am looking at it." stayed open — the single case the function's own comment cites, which
is why the gap survived review: the documented example works.

WHY THIS IS A DEFECT AND NOT A POLICY CHANGE. The rule stated in that function is that `info` closes
"ONLY when it signals completion". "Not done yet" does not signal completion. The 2026-05-31 review
(F4) settled that an `info` ack should leave the run open; a negated completion word is the same
question with the same answer.

WHY THE GUARD LEANS TOWARDS STAYING OPEN. The two errors do not cost the same. A contract that closes
too late gets a reply reminder — the system's designed recovery, and the reminder machinery exists
for exactly this. One that closes too early strands the sender believing an answer arrived, with
nothing left to nudge. So a keyword is discounted whenever the preceding words in its clause negate
it or push it into the future, and inside a question.

Both directions are asserted below, because a guard that only ever refuses is not a guard, it is an
outage: the messages that SHOULD close a contract still do.
"""

from __future__ import annotations

import unittest

from service.api_core.reply_contract import _message_satisfies_reply_contract, _signals_completion

#: Real-shaped progress updates that must NOT close a reply contract.
STAYS_OPEN = [
    "Not done yet - still investigating.",
    "I haven't finished; blocked on the DB lock.",
    "This is not fixed. Reopening.",
    "not ready yet",
    "No progress; nothing resolved so far.",
    "Still working on it, will report when done.",
    "Will push once the tests pass.",
    "Almost done, need one more pass.",
    "Not done. Not fixed. Still blocked.",
    "Are you ready for the handoff?",
    "Is this fixed on your side?",
    "Ack, I am looking at it.",          # the pre-existing behaviour, unchanged
    "Looking into it now.",
]

#: Claims of completion that must still close it. The guard must not swallow the feature.
CLOSES = [
    "Done - pushed to main.",
    "Fixed the parser; tests green.",
    "Shipped it.",
    "All merged and verified.",
    "Task complete.",
    "Investigated the lock. Resolved it.",
    "Blocked on X earlier. Fixed the parser though.",
    "Answered in the thread.",
]


class InfoCompletionSignalTests(unittest.TestCase):
    def test_a_progress_update_does_not_close_the_contract(self):
        for text in STAYS_OPEN:
            with self.subTest(text=text):
                self.assertFalse(
                    _message_satisfies_reply_contract("info", "", text),
                    "this closed the reply contract, so the sender stops waiting and no reminder is "
                    "ever sent — the answer is lost silently",
                )

    def test_a_completion_claim_still_closes_the_contract(self):
        for text in CLOSES:
            with self.subTest(text=text):
                self.assertTrue(
                    _message_satisfies_reply_contract("info", "", text),
                    "a genuine completion no longer closes the contract; the guard has swallowed "
                    "the feature and every finished run will now be nagged by reminders",
                )

    def test_the_subject_is_read_as_well_as_the_body(self):
        self.assertTrue(_message_satisfies_reply_contract("info", "Done", ""))
        self.assertFalse(_message_satisfies_reply_contract("info", "Not done", ""))

    # ── the clause boundary, which cost a false negative while writing this ──────────────────

    def test_a_negator_in_an_EARLIER_sentence_does_not_veto_a_later_claim(self):
        """The first version looked back four words regardless of punctuation, so "Blocked on X
        earlier. Fixed the parser though." reported nothing done. Look-back is clause-scoped."""
        self.assertTrue(_signals_completion("Blocked on X earlier. Fixed the parser though."))
        self.assertTrue(_signals_completion("Could not reproduce; fixed anyway."))
        self.assertFalse(_signals_completion("Fixed? Not yet."))

    def test_a_negator_in_the_SAME_clause_does_veto(self):
        self.assertFalse(_signals_completion("this is not done"))
        self.assertFalse(_signals_completion("have not yet been done"))
        self.assertFalse(_signals_completion("nothing has been fixed"))

    # ── handoff types are unaffected ─────────────────────────────────────────────────────────

    def test_handoff_reply_types_close_regardless_of_wording(self):
        """`response`/`review`/`error`/`approval` are structural answers — the keyword test never
        applied to them, and this guard must not start applying it."""
        for reply_type in ("response", "review", "error", "approval"):
            with self.subTest(reply_type=reply_type):
                self.assertTrue(
                    _message_satisfies_reply_contract(reply_type, "", "not done, still blocked"),
                    "a structural reply must close the contract on its TYPE, whatever it says",
                )

    def test_an_unknown_type_closes_nothing(self):
        self.assertFalse(_message_satisfies_reply_contract("request", "", "done"))
        self.assertFalse(_message_satisfies_reply_contract("", "", "done"))

    # ── anti-vacuity ─────────────────────────────────────────────────────────────────────────

    def test_the_signal_is_not_simply_always_false(self):
        """Every STAYS_OPEN assertion would pass against a function that returns False forever."""
        self.assertTrue(any(_signals_completion(t) for t in CLOSES))
        self.assertEqual([t for t in CLOSES if not _signals_completion(t)], [])
