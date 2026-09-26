"""A managed claude's resume menu is answered with "Resume full session as-is", and nothing else.

Claude asks, when it resumes a large session, whether to resume from a summary (which compacts the
session, and cannot be undone) or the full session as-is. The operator's policy since 2026-06-05 is the
full session. The environment bridge answered it that way until the rules moved into the service on
2026-09-03, which refused the menu outright instead, so a managed claude restarted onto a large session
sat at the menu until someone answered it in the console (v0.7.4 restores the answer).

WHAT THESE PIN, mostly about NOT answering: the menu's order has changed upstream before (2026-08-01,
summary moved to option 1 and a third option appeared), and a progressively painted menu can show the
summary line before the full-session line. So the answer is computed from where the cursor IS and where
"Resume full session" IS on the rendered screen, and it presses nothing unless both are there.
"""

from __future__ import annotations

import unittest

from service.api_core.console_prompts import DOWN, ENTER, UP, answer_for_screen

OPTION_GLYPHS = "❯›▶"


def lands_on(screen: str, keys: str) -> str:
    """The option row a keystroke sequence leaves the cursor on, simulated against the screen."""
    rows = [l for l in screen.splitlines()
            if any(t in l for t in ("Resume from summary", "Resume full session", "Don't ask me again", "Dont ask me again"))]
    at = next(i for i, l in enumerate(rows) if any(g in l for g in OPTION_GLYPHS))
    assert keys.endswith(ENTER), keys
    moves = keys[: -len(ENTER)]
    at += moves.count(DOWN) - moves.count(UP)
    return rows[at]


NUMBERED_SUMMARY_FIRST = (
    "This session is 2h 14m old and 180k tokens.\n\n"
    "❯ 1. Resume from summary (recommended)\n"
    "  2. Resume full session as-is\n"
    "  3. Don't ask me again\n"
)
UNNUMBERED = (
    "Resume session?\n"
    "❯ Resume from summary\n"
    "  Resume full session as-is\n"
    "  Don't ask me again\n"
)


class AResumeMenuIsAnsweredWithFullSessionTests(unittest.TestCase):
    def test_the_current_numbered_menu_lands_on_full_session(self):
        answer = answer_for_screen(NUMBERED_SUMMARY_FIRST, resume_policy="native_first")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.rule, "resume-full-session")
        self.assertIn("Resume full session as-is", lands_on(NUMBERED_SUMMARY_FIRST, answer.keys))

    def test_the_unnumbered_menu_lands_on_full_session(self):
        answer = answer_for_screen(UNNUMBERED, resume_policy="native_first")
        self.assertIn("Resume full session as-is", lands_on(UNNUMBERED, answer.keys))

    def test_the_old_order_full_session_first_is_just_enter(self):
        old = "❯ 1. Resume full session as-is\n  2. Resume from summary\n"
        self.assertEqual(answer_for_screen(old, resume_policy="native_first").keys, ENTER)

    def test_a_cursor_below_full_session_moves_up(self):
        below = "  1. Resume from summary\n  2. Resume full session as-is\n❯ 3. Don't ask me again\n"
        answer = answer_for_screen(below, resume_policy="native_first")
        self.assertEqual(answer.keys, UP + ENTER)
        self.assertIn("Resume full session as-is", lands_on(below, answer.keys))

    def test_no_policy_is_the_operators_default_keep_the_session(self):
        # An agent row with no resumePolicy reads as "" (test_the_prompt_rule_gets_the_terminals_resume_policy).
        self.assertIsNotNone(answer_for_screen(NUMBERED_SUMMARY_FIRST))

    def test_A_HALF_PAINTED_MENU_IS_NOT_ANSWERED(self):
        """The summary line paints first. Pressing Enter now would select it."""
        partial = "This session is large.\n\n❯ 1. Resume from summary (recommended)\n"
        self.assertIsNone(answer_for_screen(partial, resume_policy="native_first"))

    def test_no_cursor_on_an_option_is_not_answered(self):
        no_cursor = "  1. Resume from summary\n  2. Resume full session as-is\n"
        self.assertIsNone(answer_for_screen(no_cursor, resume_policy="native_first"))

    def test_a_fresh_context_agent_is_not_answered(self):
        """A Reset starts fresh and carries no resume handle, so this menu should not appear; if it does,
        choosing for it is not ours to decide."""
        self.assertIsNone(answer_for_screen(NUMBERED_SUMMARY_FIRST, resume_policy="fresh_context"))

    def test_the_answer_never_selects_summary_or_dont_ask(self):
        for screen in (NUMBERED_SUMMARY_FIRST, UNNUMBERED):
            landed = lands_on(screen, answer_for_screen(screen, resume_policy="native_first").keys)
            self.assertNotIn("summary", landed.lower())
            self.assertNotIn("ask me again", landed.lower())
