"""The awaiting-input guard reads claude's turn footer by the one set of rules (2026-10-14 audit, S3).

`console_working.py` (deleted in 0.9.9 S3b) knew the current running footer, `✻ Verb… (2m 3s · ↓ 1.2k tokens)`;
`terminal_text.py`'s awaiting-input guard knew only the older `esc to interrupt` footer and the finished
`✻ Worked for 3m`. So while claude worked under the current footer, the guard found no footer and read
a subagent's "which option" prose above it as a live question -- the 2026-06-07 incident, back by drift.
"""

from __future__ import annotations

import unittest

from service.api_core.claude_footer import RUNNING_FOOTER
from service.api_core.terminal_text import _terminal_awaiting_input_hint

PROSE = "Subagent report: which option should we take, A or B? I lean A.\n"
RUNNING = "✻ Actualizing… (2m 3s · ↓ 1.2k tokens)\n"


class OneOwnerReadsTheClaudeFooter(unittest.TestCase):
    def test_prose_above_the_running_footer_is_not_a_question(self):
        self.assertTrue(RUNNING_FOOTER.search(RUNNING), "control: the running rule sees this footer")
        self.assertEqual(_terminal_awaiting_input_hint(PROSE + RUNNING), "",
                         "a claude working under the current footer read as awaiting input")

    def test_the_same_prose_with_no_footer_still_reads_as_a_question(self):
        """Control: the guard still finds a live question when nothing says claude is working."""
        self.assertEqual(_terminal_awaiting_input_hint(PROSE), "Awaiting console input.")

    def test_a_question_after_a_finished_turn_is_live(self):
        """The finished footer ends the turn; a question asked after it is the current state."""
        self.assertFalse(RUNNING_FOOTER.search("✻ Worked for 3m 12s\n"), "control: finished is not running")
        self.assertEqual(_terminal_awaiting_input_hint("✻ Worked for 3m 12s\n" + PROSE), "Awaiting console input.")


if __name__ == "__main__":
    unittest.main()
