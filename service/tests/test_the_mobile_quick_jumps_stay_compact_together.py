"""Both quick-jump rows get the compact phone treatment, or neither does.

THE DEFECT WAS A TWIN LEFT BEHIND. `styles.css` turns `.button-row` into a grid below 760px, which
makes every button in it full-width and stacks them. Someone noticed what that does to a phone and
fixed it -- for `.attention-strip .button-row` only, with a comment saying why:

    The attention-strip's quick-jump buttons are short — wrap them in a row instead of three
    full-width stacked rows, so the strip header stays compact on a phone.

The HEADER's own row, `.button-row.top-quick-jumps`, holds Work / Spawn / Settings / Notify / Refresh
and has exactly the same problem. It was not included, and nothing said the two belonged together.

MEASURED in a 390x780 viewport before changing anything: five buttons, each 362px wide, stacked at
50px intervals, putting the first content 481px down a 780px screen -- 62% of the first screen is
chrome. With the same treatment applied: 381px, 49%, ONE row, and no horizontal overflow. 100px of a
phone screen, for a selector.

WHY THIS TEST AND NOT A SCREENSHOT. The failure mode is divergence, not appearance: one row gets a
compact rule and its twin quietly does not, which is how it happened the first time. So this asserts
they are governed TOGETHER -- if a future change gives one of them a different display, wrap or flex
treatment inside the phone breakpoint, this fails and names the pair. It deliberately does not pin the
VALUES, because how compact they are is a design choice and only their agreement is the invariant.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CSS = Path(__file__).resolve().parent.parent / "new_dashboard" / "styles.css"
TEXT = CSS.read_text(encoding="utf-8")

#: The breakpoint whose `.button-row { display: grid }` causes the stacking.
#:
#: IT IS 414, NOT 760, and I had it wrong first. Both apply on a 390px screen, so a browser probe
#: measured the improvement correctly either way and only this file's positive control noticed --
#: it reported the stacking rule as absent from a block it was never in.
_PHONE = "@media (max-width: 414px)"


def _phone_block() -> str:
    """Every phone media query's body, concatenated, by balancing braces from each opening one.

    THE BREAKPOINT REPEATS, and taking only the first occurrence is the mistake this file was written
    to be about. `styles.css` opens the same media query several times, so a version of this helper
    that stopped at `TEXT.index(...)` judged one arbitrary block and reported the rules it wanted as
    absent. Both that and the wrong breakpoint were caught by the positive control below rather than
    by reading, which is the only reason neither is still here.
    """
    bodies = []
    for start in [m for m in range(len(TEXT)) if TEXT.startswith(_PHONE, m)]:
        open_brace = TEXT.index("{", start)
        depth = 0
        for i in range(open_brace, len(TEXT)):
            if TEXT[i] == "{":
                depth += 1
            elif TEXT[i] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(TEXT[open_brace + 1:i])
                    break
        else:
            raise AssertionError("a phone media query is unbalanced")
    if not bodies:
        raise AssertionError("no phone media query found")
    return chr(10).join(bodies)


BLOCK = _phone_block()


def _declarations_for(selector: str) -> list:
    """Every declaration block whose selector list mentions `selector`, inside the phone block."""
    out = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", BLOCK):
        if selector in rule.group(1):
            out.append(" ".join(rule.group(2).split()))
    return out


class TheMobileQuickJumpsStayCompactTogether(unittest.TestCase):
    def test_the_phone_block_was_actually_found(self):
        """POSITIVE CONTROL. A brace-balance that returned an empty string would make every assertion
        below pass against nothing -- the failure this whole file is about, one level up."""
        self.assertIn(_PHONE, TEXT, "the phone breakpoint has been renamed or removed")
        self.assertGreater(len(BLOCK), 500, "the phone media block came back suspiciously small")
        self.assertIn(".button-row", BLOCK, "the rule that causes the stacking is not in this block")

    def test_the_stacking_rule_is_still_what_makes_this_necessary(self):
        """The premise. If `.button-row` stops becoming a grid on phones, both overrides are dead
        weight and this test should be deleted rather than kept passing."""
        self.assertTrue(
            any("grid" in d for d in _declarations_for(".button-row")),
            "no .button-row rule sets a grid on phones; the compact overrides may no longer be needed",
        )

    def test_EVERY_row_measured_as_compactable_is_named(self):
        """The three whose buttons FIT beside each other, measured in a 390x780 viewport.

        `.button-row` is a grid on phones, so every row in it stacks full-width by default. That is
        RIGHT for long labels and wrong for short ones, and the only way to tell them apart is to
        measure whether they fit -- which is what each of these was.
        """
        for selector, saved in (
            (".attention-strip .button-row", "the strip header"),
            (".button-row.top-quick-jumps", "481px -> 381px of a 780px screen, 5 rows -> 1"),
            ("#page-settings .section-head .button-row", "92px -> 42px, 2 rows -> 1"),
        ):
            with self.subTest(selector=selector):
                self.assertTrue(
                    _declarations_for(selector),
                    f"{selector} lost its compact phone rule and will stack full-width again ({saved})",
                )

    def test_the_LONG_labelled_row_is_deliberately_left_stacking(self):
        """THE ONE THAT MEASURED BADLY, pinned so nobody "completes the set" without re-measuring.

        `.diagnostics-maintenance` holds "Repair delivered reads" and "Repair handoffs". Wrapping it
        saves 120px -> 92px and still needs TWO lines, with the second button forced to a full 362px --
        a ragged layout for 28px. Long labels are exactly the case the original comment says should
        stack, so it is excluded ON PURPOSE rather than overlooked.

        If someone measures it again and disagrees, the right move is to add the selector AND change
        this test, not to discover the exclusion by accident.
        """
        self.assertFalse(
            _declarations_for(".diagnostics-maintenance"),
            "`.diagnostics-maintenance` gained a compact phone rule. That was measured and rejected: "
            "it saves 28px and still wraps to two lines with one full-width button. If the labels got "
            "shorter, re-measure and update this test with the new numbers.",
        )

    def test_they_are_governed_the_SAME_way(self):
        """The real invariant, and the one whose absence caused this. Divergence is the defect; the
        particular values are a design choice."""
        self.assertEqual(
            sorted(_declarations_for(".attention-strip .button-row")),
            sorted(_declarations_for(".button-row.top-quick-jumps")),
            "the two quick-jump rows are styled differently on phones again; that divergence is how "
            "the header row was left stacking in the first place",
        )


if __name__ == "__main__":
    unittest.main()
