"""The resume-menu answer fires on the menu claude draws, never on the menu's text quoted elsewhere.

THE DEFECT (external review, 2026-09-29, F5). The rule looked for option rows anywhere on the
screen. A live agent that greps this repo's own resume-menu fixtures, cats one, or quotes the menu in
a reply has those rows in its transcript, and the new claude input prompt draws the same `❯` glyph
the menu uses as its cursor. The service then typed arrow keys and Enter into a working agent's
input box: Down walks the prompt history and Enter submits whatever is in the box.

WHERE CLAUDE DRAWS A DIALOG. The development-channels dialog captured from a live worker on
2026-09-03 (`service/tests/data/claude-dev-channels-prompt.raw.txt`, rendered by pyte) ends with its
option rows, a blank line and `Enter to confirm · Esc to cancel`, and nothing below. A claude with a
transcript on screen always has its input box and footer below the transcript. So a menu that is the
live thing on screen is the LAST block drawn, and quoted rows are never last. The screens below go
through the service's own renderer (`feed_live_screen` / `render_live_screen`), because that is what
production hands the rule.

The resume menu itself has not been captured live (KNOWN_ISSUES.md); the real-menu screen here uses
the captured dialog's layout, which is ASSUMED to be the resume menu's too.
"""

from __future__ import annotations

import unittest

from service.api_core.console_prompts import DOWN, ENTER, answer_for_screen, plain_text
from service.terminal_snapshot import feed_live_screen, render_live_screen

_SGR = chr(27) + "[0m"  # feed_live_screen creates no screen for a first chunk without an ESC.

#: The bottom of a live claude: the input box, drawn with the same glyph the menu uses as its cursor.
_INPUT_BOX = (
    "\r\n"
    "────────────────────────────────────────────────────────────────────────────────\r\n"
    "❯ \r\n"
    "────────────────────────────────────────────────────────────────────────────────\r\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\r\n"
)

#: A working agent that grepped the resume-menu test fixtures.
GREP_OF_A_TEST_FILE = _SGR + (
    '● Bash(grep -n "Resume" service/tests/test_a_resume_menu_is_answered_with_full_session.py)\r\n'
    '  ⎿  37:    "❯ 1. Resume from summary (recommended)\\n"\r\n'
    '     38:    "  2. Resume full session as-is\\n"\r\n'
    "     … +14 lines (ctrl+o to expand)\r\n"
) + _INPUT_BOX

#: A working agent that printed the fixture file: the quoted rows start their lines, as the menu's do.
CAT_OF_THE_FIXTURE = _SGR + (
    "● Bash(cat mcp/stdio/tests/fixtures/claude-console/resume-prompt.txt)\r\n"
    "  ⎿  Resume session?\r\n"
    "\r\n"
    "     ❯ 1. Resume from summary (recommended)\r\n"
    "       2. Resume full session as-is\r\n"
) + _INPUT_BOX

#: A working agent quoting the menu in its own reply.
QUOTED_IN_A_REPLY = _SGR + (
    "● The worker is parked at the resume menu, which reads:\r\n"
    "\r\n"
    "  ❯ 1. Resume from summary (recommended)\r\n"
    "    2. Resume full session as-is\r\n"
    "    3. Don't ask me again\r\n"
    "\r\n"
    "  so I will answer it from the console.\r\n"
) + _INPUT_BOX

#: A plain shell that printed the fixture: its prompt follows the rows.
SHELL_CAT = _SGR + (
    "$ cat resume-prompt.txt\r\n"
    "Resume session?\r\n"
    "\r\n"
    "❯ 1. Resume from summary (recommended)\r\n"
    "  2. Resume full session as-is\r\n"
    "$ \r\n"
)

#: The menu itself, laid out as the captured development-channels dialog is.
THE_REAL_MENU = _SGR + (
    "\r\n"
    "  This session is 2h 14m old and 180k tokens.\r\n"
    "\r\n"
    "  ❯ 1. Resume from summary (recommended)\r\n"
    "    2. Resume full session as-is\r\n"
    "    3. Don't ask me again\r\n"
    "\r\n"
    "  Enter to confirm · Esc to cancel\r\n"
)


#: The same class for the one other dialog the service answers: an agent grepped the dev-channels
#: fixture, under the `│ > │` input box `mcp/stdio/tests/fixtures/claude-console/idle-prompt.txt`
#: records, which draws no cursor glyph of its own. The old rule took the quoted row as the cursor.
GREP_OF_THE_DEV_CHANNELS_FIXTURE = _SGR + (
    "● Bash(grep -n . mcp/stdio/tests/fixtures/claude-console/dev-channels-accept.txt)\r\n"
    "  ⎿  10:  ❯ 1. I am using this for local development\r\n"
    "     11:    2. Exit\r\n"
    "     13:  Enter to confirm · Esc to cancel\r\n"
    "\r\n"
    "╭──────────────────────────────────────────────╮\r\n"
    "│ >                                            │\r\n"
    "╰──────────────────────────────────────────────╯\r\n"
    "  ? for shortcuts\r\n"
)


def rendered(raw: str, terminal_id: str) -> str:
    feed_live_screen(terminal_id, raw, cols=120, rows=30)
    screen = render_live_screen(terminal_id)
    return screen[0] if screen else ""


class AResumeMenuIsAnsweredOnlyWhereClaudeDrawsItTests(unittest.TestCase):
    def _screen(self, raw: str, name: str) -> str:
        screen = rendered(raw, f"t-anchor-{name}")
        if not screen:
            self.skipTest("pyte is not installed, so this service renders no live screen")
        # CONTROL: the quoted rows really are on the rendered screen, so a None below is the rule
        # refusing them rather than the renderer losing them.
        text = plain_text(screen)
        self.assertIn("Resume from summary", text)
        self.assertIn("Resume full session", text)
        return screen

    def test_a_grep_of_a_test_file_is_not_answered(self):
        self.assertIsNone(answer_for_screen(self._screen(GREP_OF_A_TEST_FILE, "grep")))

    def test_a_cat_of_the_fixture_is_not_answered(self):
        self.assertIsNone(answer_for_screen(self._screen(CAT_OF_THE_FIXTURE, "cat")))

    def test_the_menu_quoted_in_a_reply_is_not_answered(self):
        self.assertIsNone(answer_for_screen(self._screen(QUOTED_IN_A_REPLY, "quoted")))

    def test_a_shell_that_printed_the_menu_is_not_answered(self):
        self.assertIsNone(answer_for_screen(self._screen(SHELL_CAT, "shell")))

    def test_a_grep_of_the_dev_channels_fixture_is_not_answered(self):
        screen = rendered(GREP_OF_THE_DEV_CHANNELS_FIXTURE, "t-anchor-dev")
        if not screen:
            self.skipTest("pyte is not installed, so this service renders no live screen")
        self.assertIn("I am using this for local development", plain_text(screen))  # control
        self.assertIsNone(answer_for_screen(screen))

    def test_the_real_menu_is_answered_with_full_session(self):
        """The other side: the anchor must not refuse the screen it exists to answer."""
        answer = answer_for_screen(self._screen(THE_REAL_MENU, "real"), resume_policy="native_first")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.rule, "resume-full-session")
        self.assertEqual(answer.keys, DOWN + ENTER)


if __name__ == "__main__":
    unittest.main()
