"""Console dialogs a worker cannot pass on its own, decided by the tier that knows what they mean.

WHY HERE AND NOT ON THE HOST. These rules are a model of a claude SCREEN, which is a runtime
concept, and this service already owns runtime concepts. The host that runs the process must not
learn what claude looks like: it is about to run processes for aify-dashboard and
aify-project-graph too, and a host carrying one service's screen model would end up carrying all of
them. It was briefly implemented in aify-env to unblock a fleet at 5am on 2026-09-03; the operator
said that was the wrong layer, and they were right.

MATCHED AGAINST THE RENDERED SCREEN, and that is the whole reason the first attempt failed. Claude
does not send spaces, it moves the cursor:

    I<ESC>[1Cam<ESC>[1Cusing<ESC>[1Cthis<ESC>[1Cfor<ESC>[1Clocal<ESC>[1Cdevelopment

A matcher fed raw bytes looks for a string that is never transmitted. It watched the dialog it was
written for and did nothing, with fourteen green tests, because those tests had been written from
what a screen LOOKS like rather than from what a terminal sends. So the tests below drive the REAL
renderer this service already keeps -- `feed_live_screen` / `render_live_screen`, pyte -- with the
REAL bytes captured from a live worker. A hand-written screen would reproduce the same blind spot.

WHAT THESE PIN, and most of them are about NOT answering. A rule that answers eagerly is worse than
no rule: it types into whatever is on screen, and one of the screens it could meet is a resume menu
whose default silently compacts a session's entire context.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from service.api_core.console_prompts import (
    ENTER,
    answer_for_screen,
    forget_terminal,
    plain_text,
    should_answer,
)
from service.terminal_snapshot import feed_live_screen, render_live_screen

REPO = Path(__file__).resolve().parents[2]
#: Captured from aify-env's own replay buffer while a live worker sat at this dialog, 2026-09-03.
RAW_CAPTURE = REPO / "service" / "tests" / "data" / "claude-dev-channels-prompt.raw.txt"


def _rendered(raw: str, terminal_id: str) -> str:
    """Through the service's OWN renderer, which is what production uses."""
    feed_live_screen(terminal_id, raw, cols=120, rows=30)
    screen = render_live_screen(terminal_id)
    return screen[0] if screen else ""


class TheServiceAnswersAParkedConsoleTests(unittest.TestCase):
    def setUp(self):
        forget_terminal("t-render")
        forget_terminal("t-guard")

    def test_the_REAL_capture_renders_to_the_dialog_a_person_sees(self):
        """CONTROL, and the one that matters most. If the renderer does not turn these bytes into
        readable lines, every assertion below is about a screen production never produces."""
        if not RAW_CAPTURE.is_file():
            self.skipTest("raw capture fixture is absent")
        screen = _rendered(RAW_CAPTURE.read_text(encoding="utf-8"), "t-render")
        if not screen:
            self.skipTest("pyte is not installed, so this service renders no live screen")
        # THROUGH `plain_text`, because `render_live_screen` returns an ANSI-DECORATED render: pyte
        # resolves the LAYOUT -- cursor moves become real spaces, which is the hard part -- and then
        # re-emits colour codes between the words. This control caught exactly that: the first
        # version asserted on the decorated screen and failed, which is the assertion working.
        readable = plain_text(screen)
        self.assertIn("I am using this for local development", readable,
                      "the renderer did not turn cursor-moves back into readable text")
        self.assertIn("Enter to confirm", readable)

    def test_THE_REAL_CAPTURE_IS_ANSWERED(self):
        """The assertion a hand-written fixture could not make."""
        if not RAW_CAPTURE.is_file():
            self.skipTest("raw capture fixture is absent")
        screen = _rendered(RAW_CAPTURE.read_text(encoding="utf-8"), "t-render")
        if not screen:
            self.skipTest("pyte is not installed, so this service renders no live screen")
        answer = answer_for_screen(screen)
        self.assertIsNotNone(answer, "the dialog that parks every fresh worker was not answered")
        self.assertEqual(answer.keys, ENTER)
        self.assertEqual(answer.rule, "dev-channels-accept")

    def test_THE_RAW_BYTES_DO_NOT_CONTAIN_THE_PHRASE(self):
        """The control for the control. If this becomes true, claude changed how it renders and the
        renderer is no longer what makes this work -- somebody should find out why before trusting
        it. This is the exact assertion that would have caught the first attempt in minutes."""
        if not RAW_CAPTURE.is_file():
            self.skipTest("raw capture fixture is absent")
        raw = RAW_CAPTURE.read_text(encoding="utf-8")
        self.assertNotIn("I am using this for local development", raw)
        self.assertIn("\x1b[1C", raw, "the capture no longer uses cursor-forward for spaces")

    def test_ordinary_boot_output_naming_the_flag_is_NOT_answered(self):
        """The flag appears in every worker's command line and sits on screen while other menus
        render. Matching it rather than the dialog's own question line is how the aify-comms bridge
        came to press Enter into a resume menu on every cold start."""
        boot = "claude --dangerously-load-development-channels server:aify-comms-channel --model opus"
        self.assertIsNone(answer_for_screen(boot))

    def test_A_RESUME_MENU_SUPPRESSES_EVERYTHING(self):
        """Its highlighted default is "Resume from summary", so a stray Enter silently compacts a
        session's whole context. Refused wholesale rather than ordered by position: this is the one
        screen where a wrong keystroke is unrecoverable, and no rule here is worth that risk."""
        screen = (
            "  ❯ 1. I am using this for local development\n    2. Exit\n  Enter to confirm\n"
            "  ❯ Resume from summary (recommended)\n    Resume full session as-is\n"
        )
        self.assertIsNone(answer_for_screen(screen))

    def test_the_cursor_must_be_on_the_accepting_option(self):
        """Without this the answer is a blind Enter, which selects whatever a different menu had
        highlighted."""
        on_exit = "    1. I am using this for local development\n  ❯ 2. Exit\n  Enter to confirm\n"
        self.assertIsNone(answer_for_screen(on_exit))

    def test_the_dialogs_own_chrome_is_required(self):
        prose = "  ❯ 1. I am using this for local development\n  (no confirm line here)\n"
        self.assertIsNone(answer_for_screen(prose))

    def test_nothing_is_answered_on_an_ordinary_screen(self):
        for quiet in ("", "  ❯ 1. Yes\n    2. No\n  Enter to confirm\n", "some output\n"):
            self.assertIsNone(answer_for_screen(quiet), f"answered: {quiet!r}")

    def test_ONCE_PER_TERMINAL_PER_RULE(self):
        """`comms_console_input`'s own docs record the measurement: a repeated Enter into a stuck
        claude draft was tried five times, every call reported success, and nothing submitted. A
        loop pressing keys at a screen it cannot change is indistinguishable from one that works,
        and it types into whatever replaces that screen."""
        screen = "  ❯ 1. I am using this for local development\n    2. Exit\n  Enter to confirm\n"
        answer = answer_for_screen(screen)
        self.assertTrue(should_answer("t-guard", answer))
        self.assertFalse(should_answer("t-guard", answer))
        self.assertFalse(should_answer("t-guard", answer))

    def test_each_terminal_is_answered_on_its_own(self):
        screen = "  ❯ 1. I am using this for local development\n    2. Exit\n  Enter to confirm\n"
        answer = answer_for_screen(screen)
        self.assertTrue(should_answer("t-a", answer))
        self.assertTrue(should_answer("t-b", answer))
        forget_terminal("t-a")
        forget_terminal("t-b")

    def test_no_answer_is_never_recorded_as_answered(self):
        """A None must not consume the terminal's one chance at a real dialog later."""
        self.assertFalse(should_answer("t-guard", None))
        screen = "  ❯ 1. I am using this for local development\n    2. Exit\n  Enter to confirm\n"
        self.assertTrue(should_answer("t-guard", answer_for_screen(screen)))

    def test_forgetting_a_terminal_releases_its_record(self):
        screen = "  ❯ 1. I am using this for local development\n    2. Exit\n  Enter to confirm\n"
        answer = answer_for_screen(screen)
        self.assertTrue(should_answer("t-guard", answer))
        forget_terminal("t-guard")
        self.assertTrue(should_answer("t-guard", answer))


if __name__ == "__main__":
    unittest.main()
