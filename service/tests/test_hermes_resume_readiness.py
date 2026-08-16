"""`already` contains `ready`, and that is how a dispatch got claimed into a resuming Console.

`_hermes_terminal_still_resuming` reads a hermes wrapper Console's output and answers "is this
session still coming back up". When it says yes, `_bridge_claim_block_reason` refuses the claim with
a reason that states the contract out loud:

    "Hermes wrapper Console is still resuming a saved session; waiting for ready/heal before
     claiming channel work."

It decided by substring:

    resume_idx = compact.rfind("resuming")
    ready_idx  = compact.rfind("ready")
    return ready_idx < resume_idx

`rfind("ready")` matches inside `already`. So any output where hermes said "already" AFTER "resuming"
— "resuming session abc: session already exists", "resuming, already up to date", "already
connected" — reported the console as READY. `_terminal_text_compact` lowercases first, so `Already`
counted too. The guard then returned no reason, the claim was not blocked, and channel work went into
a Console mid-resume.

It needs no unusual output. "already" is one of the most common words a CLI prints while reconnecting
to something that exists, which is precisely the situation this predicate is asked about.

THE FIX IS WORD BOUNDARIES, and it moves in the conservative direction: a console that only ever says
"already" now reads as still-resuming, so the run stays queued and retries rather than being
delivered into a resuming session. That is bounded rather than a hang — the bridge's own
`hermesResumeStallHealMs` (30s) restarts a resume that never reaches ready.

THE MODULE WAS 3/4 UNTESTED. Only `_bridge_claim_block_reason` had any coverage; the three helpers
that decide what it returns had none, and this one is pure — a string in, a bool out.
"""

from __future__ import annotations

import unittest

from service.api_core.claim_block_reason import (
    _READY_TOKEN_RE,
    _RESUMING_TOKEN_RE,
    _hermes_terminal_still_resuming,
    _last_token_index,
)


class HermesResumeReadinessTests(unittest.TestCase):
    def test_already_does_not_count_as_ready(self):
        """THE ONE THAT MATTERS. Every line here reported READY before the fix."""
        for output in (
            "resuming session abc: session already exists",
            "resuming session abc, already up to date",
            "resuming... already resumed, waiting",
            "resuming session\nAlready connected",
            "RESUMING SESSION — ALREADY RUNNING",
        ):
            with self.subTest(output=output):
                self.assertTrue(
                    _hermes_terminal_still_resuming(output),
                    "`already` contains `ready`; it must not end the resume window",
                )

    def test_a_real_ready_still_ends_the_resume_window(self):
        """The other direction, or the fix would just block every hermes claim forever."""
        for output in (
            "resuming session abc ... ready",
            "resuming session -> ready.",
            "resuming session [ready]",
            "resuming session\nsession ready",
            "resuming, already exists, then ready",
        ):
            with self.subTest(output=output):
                self.assertFalse(_hermes_terminal_still_resuming(output))

    def test_the_last_occurrence_of_each_word_decides(self):
        """The predicate compares POSITIONS, so a stale earlier `ready` must not win.

        A Console buffer holds the whole session, so an old `ready` from a previous resume is
        normal — what matters is whether a `ready` came after the most recent `resuming`.
        """
        self.assertFalse(_hermes_terminal_still_resuming("resuming ... ready ... resuming ... ready"))
        self.assertTrue(_hermes_terminal_still_resuming("ready ... resuming"))
        self.assertTrue(_hermes_terminal_still_resuming("ready ... resuming ... resuming"))

    def test_no_resume_at_all_is_not_resuming(self):
        for output in ("", "   ", "ready", "hermes started", None):
            with self.subTest(output=output):
                self.assertFalse(_hermes_terminal_still_resuming(output))

    def test_ansi_and_whitespace_are_compacted_before_matching(self):
        """The input is raw PTY output. Escape codes between the words must not hide them."""
        self.assertTrue(_hermes_terminal_still_resuming("\x1b[32mresuming\x1b[0m   session"))
        self.assertFalse(_hermes_terminal_still_resuming("\x1b[32mresuming\x1b[0m\r\n\x1b[1mready\x1b[0m"))
        # An escape INSIDE a word splits it: the compactor replaces the code with a SPACE, so
        # `re<esc>suming` becomes `re suming` and is correctly not the token. Pinned because the
        # obvious alternative — stripping the code to "" — would silently glue unrelated words
        # together and manufacture matches out of two halves that were never one word.
        self.assertFalse(_hermes_terminal_still_resuming("re\x1b[0msuming"))

    def test_neither_word_matches_inside_a_longer_word(self):
        """Both tokens are word-bounded now; the asymmetry that caused this was `ready` alone."""
        self.assertEqual(_last_token_index(_READY_TOKEN_RE, "already"), -1)
        self.assertEqual(_last_token_index(_READY_TOKEN_RE, "unready"), -1)
        self.assertEqual(_last_token_index(_READY_TOKEN_RE, "readying"), -1)
        self.assertEqual(_last_token_index(_READY_TOKEN_RE, "ready"), 0)
        self.assertEqual(_last_token_index(_READY_TOKEN_RE, "is ready now"), 3)
        self.assertEqual(_last_token_index(_RESUMING_TOKEN_RE, "unresuming"), -1)
        self.assertEqual(_last_token_index(_RESUMING_TOKEN_RE, "resuming"), 0)

    def test_the_last_token_helper_is_rfind_without_the_substring_bug(self):
        """It replaces `str.rfind`, so the contract is: same answer, whole words only."""
        text = "ready resuming ready"
        self.assertEqual(_last_token_index(_READY_TOKEN_RE, text), text.rfind("ready"))
        # …and where they differ is exactly the defect.
        bug = "resuming already"
        self.assertEqual(bug.rfind("ready"), 11, "rfind finds it inside `already`")
        self.assertEqual(_last_token_index(_READY_TOKEN_RE, bug), -1, "the word is not there")
